-- PZ Live Map (servidor): en la partida del que hostea escribe a TODOS los jugadores conectados
-- y reparte los pings que llegan del mapa web. También corre en un jugador (sin multijugador).
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

local function deliverPings()
    local ok, pings = pcall(PZLiveMap.readInbox)
    if not ok or not pings then return end
    for _, ping in ipairs(pings) do
        if isServer() then
            sendServerCommand("PZLiveMap", "ping", ping) -- a todos los jugadores conectados
        else
            PZLiveMap.showPing(ping) -- un jugador
        end
    end
end

local function tick()
    pcall(PZLiveMap.write, "pzlivemap.json", isServer() and "server" or "local", collect())
    deliverPings()
end

Events.EveryOneMinute.Add(tick)
