using Game;
using Game.Modding;

namespace CS2MCP
{
    public sealed class Mod : IMod
    {
        public void OnLoad(UpdateSystem updateSystem)
        {
            Log.Init();
            Log.Info($"CS2MCP {Bridge.Version} loading");
            updateSystem.UpdateAt<McpBridgeSystem>(SystemUpdatePhase.MainLoop);
            updateSystem.UpdateAt<McpToolSystem>(SystemUpdatePhase.ToolUpdate);
            try { Bridge.Start(); }
            catch (System.Exception e) { Log.Info("bridge failed to start: " + e); }
        }

        public void OnDispose()
        {
            Bridge.Stop();
        }
    }
}
