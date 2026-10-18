-- PZ Live Map (cliente): cada jugador escribe su propia posición. Sirve de respaldo si el
-- archivo del servidor no está disponible, y para que un amigo también pueda enviar al mapa.
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

Events.EveryOneMinute.Add(tick)
