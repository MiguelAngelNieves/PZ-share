-- PZ Live Map (cliente): cada jugador escribe su propia posición (respaldo si el archivo del
-- servidor no está, y para las fotos de PZ Share) y muestra los pings que reparte el servidor.
if not isClient() then return end
require "PZLiveMap_Shared"

local function tick()
    local list = {}
    for i = 0, getNumActivePlayers() - 1 do
        local p = getSpecificPlayer(i)
        if p then list[#list + 1] = p end
    end
    pcall(PZLiveMap.write, "pzlivemap_client.json", "client", list)
end

local function onServerCommand(module, command, args)
    if module == "PZLiveMap" and command == "ping" then pcall(PZLiveMap.showPing, args) end
end

Events.EveryOneMinute.Add(tick)
Events.OnServerCommand.Add(onServerCommand)
