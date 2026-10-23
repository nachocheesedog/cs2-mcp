// Local-only HTTP bridge. Listens on http://127.0.0.1:17654/ (never on external interfaces).
//   GET  /health          -> answered immediately from the listener thread
//   POST /rpc {"method":..., "params":{...}} -> queued, executed on the game's main thread by McpBridgeSystem,
//                                              which completes the request (possibly several frames later).
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;

namespace CS2MCP
{
    public sealed class Command
    {
        public string Method;
        public Dictionary<string, object> Params;
        public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false);
        public object Result;
        public string Error;
        public void Complete(object result) { Result = result; Done.Set(); }
        public void Fail(string error) { Error = error; Done.Set(); }
        // v0.33: a request that times out is dropped if the game has not started it yet (it used to run whenever the queue
        // drained - after the caller retried, or after another session loaded a different city). One that already started
        // is marked Abandoned: the tool then discards its preview instead of applying it.
        int m_State;                                                                         // 0 queued, 1 started, 2 dropped
        public volatile bool Abandoned;
        public bool TryStart() => Interlocked.CompareExchange(ref m_State, 1, 0) == 0;       // main thread, before dispatch
        public bool Drop() { if (Interlocked.CompareExchange(ref m_State, 2, 0) == 0) return true; Abandoned = true; return false; }
    }

    public static class Log
    {
        static readonly object Gate = new object();
        static string _path;
        public static void Init()
        {
            try
            {
                var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "LocalLow", "Colossal Order", "Cities Skylines II", "Logs");
                Directory.CreateDirectory(dir); _path = Path.Combine(dir, "CS2MCP.log");
                // keep the previous session's log: after a crash it holds the last commands that reached the game
                try { if (File.Exists(_path)) File.Copy(_path, Path.Combine(dir, "CS2MCP-prev.log"), true); } catch { }
                File.WriteAllText(_path, "");
            }
            catch { _path = null; }
        }
        public static void Info(string msg)
        {
            if (_path == null) return;
            lock (Gate) { try { File.AppendAllText(_path, DateTime.Now.ToString("HH:mm:ss.fff") + " " + msg + Environment.NewLine); } catch { } }
        }
    }

    public static class Bridge
    {
        public const int Port = 17654;
        public const string Version = "0.45.0";
        const long kMaxBody = 16L << 20;                                                     // 16 MB request body cap
        public static readonly ConcurrentQueue<Command> Inbox = new ConcurrentQueue<Command>();
        static HttpListener _listener;
        static Thread _thread;
        static volatile bool _running;

        public static void Start()
        {
            if (_running) return;
            _listener = new HttpListener();
            _listener.Prefixes.Add($"http://127.0.0.1:{Port}/");
            _listener.Start();
            _running = true;
            _thread = new Thread(Loop) { IsBackground = true, Name = "CS2MCP-HTTP" };
            _thread.Start();
            Log.Info($"bridge listening on 127.0.0.1:{Port}");
        }

        public static void Stop()
        {
            _running = false;
            try { _listener?.Stop(); _listener?.Close(); } catch { }
            while (Inbox.TryDequeue(out var c)) c.Fail("the CS2MCP bridge is shutting down");
            Log.Info("bridge stopped");
        }

        static void Loop()
        {
            while (_running)
            {
                HttpListenerContext ctx;
                try { ctx = _listener.GetContext(); }
                catch { if (!_running) return; continue; }
                // own thread per request (v0.33): a request blocks for up to timeout_s, which must not park the shared
                // ThreadPool workers the game's own async work (asset import, saving) runs on
                try { new Thread(() => Handle(ctx)) { IsBackground = true, Name = "CS2MCP-request" }.Start(); }
                catch (Exception e) { Log.Info("request thread failed: " + e.Message); try { ctx.Response.Abort(); } catch { } }
            }
        }

        // v0.33 (CSRF): a web page in the user's browser can POST to 127.0.0.1 as a "simple request" (text/plain, no
        // preflight). Browsers always send Origin (and Sec-Fetch-*) on such requests and cannot send
        // Content-Type: application/json without a CORS preflight, which this listener never approves. Every real client
        // (the MCP server, scripts) sends application/json and no Origin.
        static string RejectBrowser(HttpListenerRequest rq)
        {
            if (rq.Headers["Origin"] != null || rq.Headers["Sec-Fetch-Site"] != null || rq.Headers["Sec-Fetch-Mode"] != null)
                return "browser requests are refused";
            var ct = rq.ContentType;
            if (ct == null || !ct.TrimStart().StartsWith("application/json", StringComparison.OrdinalIgnoreCase))
                return "Content-Type must be application/json";
            return null;
        }

        static string ReadBody(HttpListenerRequest rq)
        {
            if (rq.ContentLength64 > kMaxBody) throw new InvalidDataException("request body too large");
            using (var r = new StreamReader(rq.InputStream, Encoding.UTF8))
            {
                var buf = new char[8192]; var sb = new StringBuilder(); int n;
                while ((n = r.Read(buf, 0, buf.Length)) > 0)
                {
                    sb.Append(buf, 0, n);
                    if (sb.Length > kMaxBody) throw new InvalidDataException("request body too large");
                }
                return sb.ToString();
            }
        }

        static void Handle(HttpListenerContext ctx)
        {
            var resp = new Dictionary<string, object>();
            int status = 200;
            try
            {
                if (!IPAddress.IsLoopback(ctx.Request.RemoteEndPoint.Address)) { status = 403; resp["error"] = "loopback only"; }
                else if (ctx.Request.HttpMethod == "GET" && ctx.Request.Url.AbsolutePath == "/health")
                {
                    resp["ok"] = true; resp["version"] = Version;
                }
                else if (ctx.Request.HttpMethod == "POST" && ctx.Request.Url.AbsolutePath == "/rpc")
                {
                    string refuse = RejectBrowser(ctx.Request);
                    if (refuse != null)
                    {
                        status = refuse.StartsWith("Content-Type") ? 415 : 403; resp["error"] = refuse;
                        Log.Info($"refused /rpc request: {refuse} (origin={ctx.Request.Headers["Origin"] ?? "-"})");
                    }
                    else
                    {
                        var req = Json.Parse(ReadBody(ctx.Request)) as Dictionary<string, object>;
                        var cmd = new Command { Method = Json.Str(req, "method", ""), Params = req != null && req.TryGetValue("params", out var p) ? p as Dictionary<string, object> : null };
                        if (cmd.Params == null) cmd.Params = new Dictionary<string, object>();
                        double timeout = Json.Num(cmd.Params, "timeout_s", 60);                 // validated BEFORE queueing
                        if (double.IsNaN(timeout) || timeout <= 0) timeout = 60;
                        timeout = Math.Min(timeout, 3600);
                        Inbox.Enqueue(cmd);
                        if (!cmd.Done.Wait(TimeSpan.FromSeconds(timeout)))
                        {
                            status = 504;
                            if (cmd.Drop())
                            {
                                resp["error"] = "timed out waiting for the game (is a city loaded and the game unpaused/not minimised?) - the command was NOT run (dropped)";
                                resp["executed"] = false;
                            }
                            else
                            {
                                resp["error"] = "timed out while the game was running the command - an edit that had not been applied yet is discarded; check history/status before retrying";
                                resp["executed"] = "unknown";
                            }
                            Log.Info($"{cmd.Method}: timed out after {timeout}s ({(resp["executed"] is bool ? "dropped" : "abandoned while running")})");
                        }
                        else if (cmd.Error != null) { status = 400; resp["error"] = cmd.Error; }
                        else resp["result"] = cmd.Result;
                    }
                }
                else { status = 404; resp["error"] = "unknown endpoint"; }
            }
            catch (Exception e) { status = 500; resp["error"] = e.GetType().Name + ": " + e.Message; Log.Info("handler error: " + e); }
            try
            {
                var bytes = Encoding.UTF8.GetBytes(Json.Write(resp));
                ctx.Response.StatusCode = status; ctx.Response.ContentType = "application/json";
                ctx.Response.ContentLength64 = bytes.Length;
                ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
                ctx.Response.OutputStream.Close();
            }
            catch { }
        }
    }
}
