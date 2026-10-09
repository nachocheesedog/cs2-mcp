// Screenshots: ScreenCapture writes a full-size PNG at the end of a frame; over the next frames we wait for the file,
// then downscale it on the GPU (Blit into a small RenderTexture) and save a compact JPG the MCP server can return.
// clean=true (mod 0.30) = what the game's Photo Mode does before "Take photo" (PhotoModeUISystem.CaptureScreenshot +
// ScreenCaptureHelper): RenderingSystem.hideOverlay (notification icons, overlays, road names, route lines, markers)
// + the UI view off, then wait `frames` frames (temporal AA / icon fade settle) before capturing; everything is restored
// afterwards (also on failure). save_png = keep the full-resolution PNG at that path; supersize = 1..4x render scale.
using System;
using System.Collections.Generic;
using System.IO;
using Game.SceneFlow;
using Game.Rendering;
using UnityEngine;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        sealed class ShotJob
        {
            public Command Cmd; public string Png, Jpg, SavePng; public int MaxWidth, Frames, Delay, Supersize = 1; public long LastSize = -1;
            public bool Clean, Captured, PrevHideOverlay, PrevUi;
        }
        ShotJob m_Shot;
        static int s_ShotCounter;

        // v0.33: save_png may only write a .png under these roots (it used to File.Copy(overwrite) to any path a caller named).
        // More roots: one absolute folder per line in ModsData\CS2MCP\save_png_roots.txt (read on each request).
        static IEnumerable<string> SavePngRoots()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            yield return Path.Combine(home, "blender");
            yield return Path.Combine(home, "cs2-mcp");
            yield return Path.Combine(Application.persistentDataPath, "Screenshots");
            yield return Path.GetTempPath();
            string extra = Path.Combine(Application.persistentDataPath, "ModsData", "CS2MCP", "save_png_roots.txt");
            if (File.Exists(extra))
                foreach (var l in File.ReadAllLines(extra))
                    if (l.Trim().Length > 0 && Path.IsPathRooted(l.Trim())) yield return l.Trim();
        }

        static string CheckSavePng(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            string full = Path.GetFullPath(path);
            if (!string.Equals(Path.GetExtension(full), ".png", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("save_png must be a .png file path");
            foreach (var r in SavePngRoots())
            {
                string root = Path.GetFullPath(r).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
                if (full.StartsWith(root, StringComparison.OrdinalIgnoreCase)) return full;
            }
            throw new ArgumentException("save_png must be under one of: " + string.Join("; ", SavePngRoots()) +
                                        " (add folders to ModsData\\CS2MCP\\save_png_roots.txt)");
        }

        void StartScreenshot(Command cmd)
        {
            if (m_Shot != null) { cmd.Fail("a screenshot is already being taken"); return; }
            string savePng = CheckSavePng(Json.Str(cmd.Params, "save_png", null));
            var dir = Path.Combine(Path.GetTempPath(), "CS2MCP");
            Directory.CreateDirectory(dir);
            foreach (var old in Directory.GetFiles(dir, "shot_*")) { try { File.Delete(old); } catch { } }
            int n = ++s_ShotCounter;
            var s = new ShotJob
            {
                Cmd = cmd, Png = Path.Combine(dir, $"shot_{n}.png"), Jpg = Path.Combine(dir, $"shot_{n}.jpg"),
                MaxWidth = Math.Max(320, Math.Min(3840, (int)Json.Num(cmd.Params, "max_width", 1280))),
                Clean = Json.Bool(cmd.Params, "clean", false), SavePng = savePng,
                Supersize = Math.Max(1, Math.Min(4, (int)Json.Num(cmd.Params, "supersize", 1))),
            };
            if (s.Clean)
            {
                s.Delay = Math.Max(2, Math.Min(240, (int)Json.Num(cmd.Params, "frames", 30)));
                var rs = World.GetExistingSystemManaged<RenderingSystem>();
                if (rs != null) { s.PrevHideOverlay = rs.hideOverlay; rs.hideOverlay = true; }
                var ui = GameManager.instance?.userInterface;
                if (ui?.view != null) { s.PrevUi = ui.view.enabled; ui.view.enabled = false; }
            }
            m_Shot = s;
            if (s.Delay == 0) { ScreenCapture.CaptureScreenshot(s.Png, s.Supersize); s.Captured = true; }
        }

        void RestoreAfterShot(ShotJob s)
        {
            if (!s.Clean) return;
            var rs = World.GetExistingSystemManaged<RenderingSystem>();
            if (rs != null) rs.hideOverlay = s.PrevHideOverlay;
            var ui = GameManager.instance?.userInterface;
            if (ui?.view != null) ui.view.enabled = s.PrevUi;
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        void PumpScreenshot()
        {
            var s = m_Shot;
            if (s == null) return;
            if (!s.Captured)
            {
                if (--s.Delay > 0) return;
                ScreenCapture.CaptureScreenshot(s.Png, s.Supersize); s.Captured = true; return;
            }
            s.Frames++;
            if (s.Frames > 600) { m_Shot = null; RestoreAfterShot(s); s.Cmd.Fail("screenshot timed out (is the game window minimised?)"); return; }
            if (!File.Exists(s.Png)) return;
            long size = new FileInfo(s.Png).Length;
            if (size == 0 || size != s.LastSize) { s.LastSize = size; return; }        // wait until the file stops growing
            m_Shot = null;
            RestoreAfterShot(s);
            Texture2D src = null, dst = null; RenderTexture rt = null; var prev = RenderTexture.active;
            try
            {
                var bytes = File.ReadAllBytes(s.Png);
                src = new Texture2D(2, 2);
                if (!src.LoadImage(bytes)) throw new InvalidOperationException("could not decode the capture");
                int fullW = src.width, fullH = src.height;
                int w = Math.Min(s.MaxWidth, src.width), h = Math.Max(1, src.height * w / src.width);
                rt = RenderTexture.GetTemporary(w, h, 0);
                Graphics.Blit(src, rt);
                RenderTexture.active = rt;
                dst = new Texture2D(w, h, TextureFormat.RGB24, false);
                dst.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                dst.Apply();
                RenderTexture.active = prev;
                File.WriteAllBytes(s.Jpg, dst.EncodeToJPG(88));
                var res = new Dictionary<string, object> { ["path"] = s.Jpg, ["width"] = w, ["height"] = h, ["clean"] = s.Clean };
                if (!string.IsNullOrEmpty(s.SavePng))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(s.SavePng));
                    File.Copy(s.Png, s.SavePng, true);
                    res["png"] = s.SavePng; res["png_size"] = new[] { fullW, fullH };
                }
                try { File.Delete(s.Png); } catch { }
                s.Cmd.Complete(res);
            }
            catch (Exception e) { Log.Info("screenshot failed: " + e); s.Cmd.Fail("screenshot failed: " + e.Message); }
            finally
            {   // v0.33: no leaked textures / stuck RenderTexture.active when a step above throws
                if (RenderTexture.active == rt && rt != null) RenderTexture.active = prev;
                if (rt != null) RenderTexture.ReleaseTemporary(rt);
                if (src != null) UnityEngine.Object.Destroy(src);
                if (dst != null) UnityEngine.Object.Destroy(dst);
            }
        }
    }
}
