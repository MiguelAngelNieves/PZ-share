-- PZ Live Map (servidor): en la partida del que hostea escribe a TODOS los jugadores conectados.
-- También corre en un jugador (sin multijugador).
if isClient() then return end
require "PZLiveMap_Shared"

local function collect()
    local list = {}
    local arr = getOnlinePlayers and getOnlinePlayers()
    if arr and arr:size() > 0 then
        for i = 0, arr:size() - 1 do list[#list + 1] = arr:get(i) end
    else
        for i = 0, getNumActivePlayers() - 1 do
            local p = getSpecificPlayer(i)
            if p then list[#list + 1] = p end
        end
    end
    return list
end

local function tick()
    pcall(PZLiveMap.write, "pzlivemap.json", isServer() and "server" or "local", collect())
end

Events.EveryOneMinute.Add(tick)
