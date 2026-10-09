// Photo mode (mod 0.30): the game's own photo-mode property set (PhotoModeRenderSystem.photoModeProperties - exposure,
// contrast, saturation, colour filter, white balance, shadows/midtones/highlights, bloom, vignette, depth of field,
// film grain, clouds, fog, sky, time of day ...) listed and set by id, exactly like the Photo Mode panel does it:
// enable the photo-mode render volume, switch the property's override on, set its value. reset=true puts every
// property back to its default and switches the volume off again. Used with screenshot clean=true for store shots.
using System;
using System.Collections.Generic;
using Game.Rendering;
using Game.Rendering.CinematicCamera;

namespace CS2MCP
{
    public partial class McpBridgeSystem
    {
        bool m_PhotoActive;

        object PhotoMode(Dictionary<string, object> p)
        {
            var sys = World.GetExistingSystemManaged<PhotoModeRenderSystem>() ?? throw new InvalidOperationException("photo mode render system not available");
            var props = sys.photoModeProperties ?? throw new InvalidOperationException("photo mode properties not initialised (load a city)");
            var applied = new List<object>();
            if (Json.Bool(p, "reset", false))
            {
                foreach (var kv in props)
                {
                    try { kv.Value.reset?.Invoke(); kv.Value.setEnabled?.Invoke(false); } catch (Exception e) { Log.Info("photo reset " + kv.Key + ": " + e.Message); }
                }
                sys.Enable(false); m_PhotoActive = false;
            }
            if (p != null && p.TryGetValue("set", out var so) && so is Dictionary<string, object> set && set.Count > 0)
            {
                sys.Enable(true); m_PhotoActive = true;
                foreach (var kv in set)
                {
                    PhotoModeProperty pr = null;
                    foreach (var e in props) if (string.Equals(e.Key, kv.Key, StringComparison.OrdinalIgnoreCase)) { pr = e.Value; break; }
                    if (pr == null || pr.setValue == null) throw new ArgumentException("unknown or read-only photo mode property: " + kv.Key + " (list them with no arguments)");
                    float v = kv.Value is bool b ? (b ? 1f : 0f) : (float)Convert.ToDouble(kv.Value);
                    pr.setEnabled?.Invoke(true);
                    pr.setValue(v);
                    applied.Add(new Dictionary<string, object> { ["id"] = pr.id, ["value"] = SafeGet(pr) });
                }
            }
            string grp = Json.Str(p, "group", null);
            bool full = Json.Bool(p, "list", applied.Count == 0 && !Json.Bool(p, "reset", false));
            var list = new List<object>();
            if (full)
                foreach (var kv in props)
                {
                    var pr = kv.Value;
                    if (pr.setValue == null) continue;                                    // group titles
                    if (grp != null && !string.Equals(pr.group, grp, StringComparison.OrdinalIgnoreCase)) continue;
                    var d = new Dictionary<string, object> { ["id"] = pr.id, ["group"] = pr.group, ["value"] = SafeGet(pr) };
                    try { if (pr.min != null) d["min"] = pr.min(); if (pr.max != null) d["max"] = pr.max(); } catch { }
                    try { if (pr.isEnabled != null) d["overridden"] = pr.isEnabled(); if (pr.isAvailable != null) d["available"] = pr.isAvailable(); } catch { }
                    if (pr.enumType != null) d["enum"] = Enum.GetNames(pr.enumType);
                    list.Add(d);
                }
            var r = new Dictionary<string, object> { ["active"] = m_PhotoActive };
            if (applied.Count > 0) r["applied"] = applied;
            if (full) r["properties"] = list;
            return r;
        }

        static object SafeGet(PhotoModeProperty pr) { try { return pr.getValue != null ? (object)pr.getValue() : null; } catch { return null; } }
    }
}
