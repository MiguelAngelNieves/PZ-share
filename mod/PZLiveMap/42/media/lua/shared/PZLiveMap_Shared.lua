-- PZ Live Map: arma el JSON con la posición de los jugadores y lo escribe en Zomboid/Lua/.
-- PZ Share lee ese archivo mientras juegas y lo manda al mapa en vivo. El mod no toca el juego.
PZLiveMap = PZLiveMap or {}

local function esc(s)
    s = tostring(s or "")
    s = string.gsub(s, "\\", "\\\\")
    s = string.gsub(s, '"', '\\"')
    return s
end

local function r1(n)
    return math.floor((tonumber(n) or 0) * 10 + 0.5) / 10
end

local function playerName(p)
    local ok, name = pcall(function() return p:getUsername() end)
    if ok and name and name ~= "" then return name end
    ok, name = pcall(function() return p:getDisplayName() end)
    if ok and name and name ~= "" then return name end
    return "Jugador"
end

local function health(p)
    local ok, v = pcall(function() return p:getBodyDamage():getOverallBodyHealth() end)
    if ok and v then return math.floor(v) end
    return 100
end

local function vehicle(p)
    local ok, v = pcall(function()
        local veh = p:getVehicle()
        if veh then return veh:getScript():getName() end
        return nil
    end)
    if ok then return v end
    return nil
end

function PZLiveMap.playerJson(p)
    local veh = vehicle(p)
    local dead = false
    pcall(function() dead = p:isDead() end)
    return '{"name":"' .. esc(playerName(p)) .. '"'
        .. ',"x":' .. r1(p:getX()) .. ',"y":' .. r1(p:getY())
        .. ',"z":' .. math.floor(tonumber(p:getZ()) or 0)
        .. ',"hp":' .. health(p)
        .. ',"dead":' .. tostring(dead)
        .. ',"vehicle":' .. (veh and ('"' .. esc(veh) .. '"') or "null") .. '}'
end

function PZLiveMap.write(fileName, source, players)
    local parts = {}
    for _, p in ipairs(players) do
        local ok, js = pcall(PZLiveMap.playerJson, p)
        if ok and js then parts[#parts + 1] = js end
    end
    local gt = getGameTime()
    local day = math.floor((gt:getWorldAgeHours() or 0) / 24) + 1
    local json = '{"source":"' .. source .. '"'
        .. ',"world":{"day":' .. day .. ',"hour":' .. gt:getHour() .. ',"minute":' .. gt:getMinutes() .. '}'
        .. ',"players":[' .. table.concat(parts, ",") .. ']}'
    local w = getFileWriter(fileName, true, false)
    if w then
        w:write(json)
        w:close()
    end
end
