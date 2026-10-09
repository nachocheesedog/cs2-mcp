// Minimal JSON reader/writer (no dependencies). Objects -> Dictionary<string, object>, arrays -> List<object>,
// numbers -> double, strings, bools, null.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace CS2MCP
{
    public static class Json
    {
        // v0.33: nesting is capped - the parser recurses, and a stack overflow cannot be caught (it would kill the game)
        const int kMaxDepth = 64;
        public static object Parse(string s) { int i = 0; var v = Value(s, ref i, 0); Ws(s, ref i); if (i != s.Length) throw new FormatException("trailing data at " + i); return v; }

        static void Ws(string s, ref int i) { while (i < s.Length && char.IsWhiteSpace(s[i])) i++; }
        static object Value(string s, ref int i, int depth)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if ((c == '{' || c == '[') && depth >= kMaxDepth) throw new FormatException($"JSON nested deeper than {kMaxDepth} levels");
            if (c == '{') return Obj(s, ref i, depth + 1);
            if (c == '[') return Arr(s, ref i, depth + 1);
            if (c == '"') return Str(s, ref i);
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (s.Length - i >= 5 && string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "null", 0, 4) == 0) { i += 4; return null; }
            int st = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            if (st == i) throw new FormatException("bad value at " + i);
            return double.Parse(s.Substring(st, i - st), NumberStyles.Float, CultureInfo.InvariantCulture);
        }
        static Dictionary<string, object> Obj(string s, ref int i, int depth)
        {
            var d = new Dictionary<string, object>(); i++; Ws(s, ref i);
            if (s[i] == '}') { i++; return d; }
            while (true)
            {
                Ws(s, ref i); string k = Str(s, ref i); Ws(s, ref i);
                if (s[i] != ':') throw new FormatException("expected : at " + i); i++;
                d[k] = Value(s, ref i, depth); Ws(s, ref i);
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return d; }
                throw new FormatException("expected , or } at " + i);
            }
        }
        static List<object> Arr(string s, ref int i, int depth)
        {
            var l = new List<object>(); i++; Ws(s, ref i);
            if (s[i] == ']') { i++; return l; }
            while (true)
            {
                l.Add(Value(s, ref i, depth)); Ws(s, ref i);
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return l; }
                throw new FormatException("expected , or ] at " + i);
            }
        }
        static string Str(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException("expected string at " + i);
            i++; var sb = new StringBuilder();
            while (true)
            {
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }
                char e = s[i++];
                switch (e)
                {
                    case 'n': sb.Append('\n'); break; case 't': sb.Append('\t'); break; case 'r': sb.Append('\r'); break;
                    case 'b': sb.Append('\b'); break; case 'f': sb.Append('\f'); break;
                    case 'u': sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16)); i += 4; break;
                    default: sb.Append(e); break;
                }
            }
        }

        public static string Write(object v) { var sb = new StringBuilder(); W(sb, v); return sb.ToString(); }
        static void W(StringBuilder sb, object v)
        {
            switch (v)
            {
                case null: sb.Append("null"); return;
                case string s: WStr(sb, s); return;
                case bool b: sb.Append(b ? "true" : "false"); return;
                case float f: sb.Append(float.IsNaN(f) || float.IsInfinity(f) ? "null" : f.ToString("R", CultureInfo.InvariantCulture)); return;
                case double d: sb.Append(double.IsNaN(d) || double.IsInfinity(d) ? "null" : d.ToString("R", CultureInfo.InvariantCulture)); return;
                case int n: sb.Append(n.ToString(CultureInfo.InvariantCulture)); return;
                case long n: sb.Append(n.ToString(CultureInfo.InvariantCulture)); return;
                case uint n: sb.Append(n.ToString(CultureInfo.InvariantCulture)); return;
                case IDictionary<string, object> d:
                    sb.Append('{'); bool first = true;
                    foreach (var kv in d) { if (!first) sb.Append(','); first = false; WStr(sb, kv.Key); sb.Append(':'); W(sb, kv.Value); }
                    sb.Append('}'); return;
                case IEnumerable e:
                    sb.Append('['); bool f1 = true;
                    foreach (var x in e) { if (!f1) sb.Append(','); f1 = false; W(sb, x); }
                    sb.Append(']'); return;
                default: WStr(sb, v.ToString()); return;
            }
        }
        static void WStr(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break; case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break; case '\r': sb.Append("\\r"); break; case '\t': sb.Append("\\t"); break;
                    default: if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4")); else sb.Append(c); break;
                }
            }
            sb.Append('"');
        }

        // ---- typed accessors for request params
        public static double Num(Dictionary<string, object> d, string k, double def) => d != null && d.TryGetValue(k, out var v) && v is double x ? x : def;
        public static string Str(Dictionary<string, object> d, string k, string def) => d != null && d.TryGetValue(k, out var v) && v is string x ? x : def;
        public static bool Bool(Dictionary<string, object> d, string k, bool def) => d != null && d.TryGetValue(k, out var v) && v is bool x ? x : def;
        public static List<object> List(Dictionary<string, object> d, string k) => d != null && d.TryGetValue(k, out var v) ? v as List<object> : null;
    }
}
