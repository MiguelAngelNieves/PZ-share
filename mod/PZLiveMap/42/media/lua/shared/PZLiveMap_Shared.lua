-- PZ Live Map: arma el JSON con el estado de los jugadores y lo escribe en Zomboid/Lua/.
-- PZ Share lee ese archivo mientras juegas y lo manda al mapa en vivo.
-- Solo LEE el estado (lo mismo que ve cada jugador en su pantalla): nunca la infección ni nada oculto.
-- Lo único que entra al juego son los pings del mapa, como un texto sobre tu personaje.
PZLiveMap = PZLiveMap or {}

local function esc(s)
    s = tostring(s or "")
    s = string.gsub(s, "\\", "\\\\")
    s = string.gsub(s, '"', '\\"')
    s = string.gsub(s, "[%c]", " ")
    return s
end

local function r1(n)
    return math.floor((tonumber(n) or 0) * 10 + 0.5) / 10
end

local function try(f, default)
    local ok, v = pcall(f)
    if ok and v ~= nil then return v end
    return default
end

local function str(s) return s and ('"' .. esc(s) .. '"') or "null" end

local function playerName(p)
    local name = try(function() return p:getUsername() end)
    if name and name ~= "" then return name end
    name = try(function() return p:getDisplayName() end)
    if name and name ~= "" then return name end
    return "Jugador"
end

-- Estados (moodles) que el jugador ve en su pantalla. Se prueban los dos estilos de nombre (B41/B42).
local MOODLES = {
    { "hungry", "Hungry", "HUNGRY" }, { "thirst", "Thirst", "THIRST" }, { "tired", "Tired", "TIRED" },
    { "panic", "Panic", "PANIC" }, { "pain", "Pain", "PAIN" }, { "endurance", "Endurance", "ENDURANCE" },
    { "stress", "Stress", "STRESS" }, { "unhappy", "Unhappy", "UNHAPPY" }, { "sick", "Sick", "SICK" },
    { "drunk", "Drunk", "DRUNK" }, { "wet", "Wet", "WET" }, { "cold", "Hypothermia", "HYPOTHERMIA" },
    { "hot", "Hyperthermia", "HYPERTHERMIA" }, { "heavy", "HeavyLoad", "HEAVY_LOAD" },
}

local function moodleLevel(moodles, names)
    for i = 2, #names do
        local lvl = try(function()
            local t = MoodleType[names[i]]
            if t == nil then return nil end
            return moodles:getMoodleLevel(t)
        end)
        if lvl then return lvl end
    end
    return 0
end

local function moodlesJson(p)
    local m = try(function() return p:getMoodles() end)
    if not m then return "{}" end
    local parts = {}
    for _, def in ipairs(MOODLES) do
        local lvl = moodleLevel(m, def)
        if lvl and lvl > 0 then parts[#parts + 1] = '"' .. def[1] .. '":' .. math.floor(lvl) end
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

-- Heridas visibles por zona del cuerpo (como en el panel de salud del juego).
local function woundsJson(p)
    local list = try(function() return p:getBodyDamage():getBodyParts() end)
    if not list then return "[]" end
    local out = {}
    for i = 0, list:size() - 1 do
        local part = list:get(i)
        local w = {}
        if try(function() return part:bleeding() end, false) then w[#w + 1] = '"sangra"' end
        if try(function() return part:bitten() end, false) then w[#w + 1] = '"mordida"' end
        if try(function() return part:deepWounded() end, false) then w[#w + 1] = '"profunda"' end
        if try(function() return part:scratched() end, false) then w[#w + 1] = '"rasguño"' end
        if try(function() return part:isCut() end, false) then w[#w + 1] = '"corte"' end
        if try(function() return part:getFractureTime() > 0 end, false) then w[#w + 1] = '"fractura"' end
        if try(function() return part:haveBullet() end, false) then w[#w + 1] = '"bala"' end
        if #w > 0 then
            local name = try(function() return BodyPartType.ToString(part:getType()) end)
                or try(function() return tostring(part:getType()) end, "?")
            local band = try(function() return part:bandaged() end, false)
            out[#out + 1] = '{"part":' .. str(name) .. ',"w":[' .. table.concat(w, ",") .. '],"band":' .. tostring(band) .. '}'
        end
    end
    return "[" .. table.concat(out, ",") .. "]"
end

local function vehicleJson(p)
    local veh = try(function() return p:getVehicle() end)
    if not veh then return "null" end
    local name = try(function() return veh:getScript():getName() end, "Vehículo")
    local fuel = try(function()
        local tank = veh:getPartById("GasTank")
        local cap = tank:getContainerCapacity()
        if cap and cap > 0 then return math.floor(tank:getContainerContentAmount() / cap * 100 + 0.5) end
        return nil
    end)
    local driver = try(function() return veh:getDriver() == p end, false)
    return '{"name":' .. str(name) .. ',"fuel":' .. (fuel or "null") .. ',"driver":' .. tostring(driver) .. '}'
end

function PZLiveMap.playerJson(p)
    local dead = try(function() return p:isDead() end, false)
    local hp = math.floor(try(function() return p:getBodyDamage():getOverallBodyHealth() end, 100))
    local weapon = try(function()
        local it = p:getPrimaryHandItem()
        return it and it:getDisplayName() or nil
    end)
    local bag = try(function()
        local it = p:getClothingItem_Back()
        return it and it:getDisplayName() or nil
    end)
    local weight = r1(try(function() return p:getInventoryWeight() end, 0))
    local maxw = r1(try(function() return p:getMaxWeight() end, 0))
    local kills = try(function() return p:getZombieKills() end, 0)
    local hours = r1(try(function() return p:getHoursSurvived() end, 0))
    return '{"name":"' .. esc(playerName(p)) .. '"'
        .. ',"x":' .. r1(p:getX()) .. ',"y":' .. r1(p:getY())
        .. ',"z":' .. math.floor(tonumber(p:getZ()) or 0)
        .. ',"hp":' .. hp
        .. ',"dead":' .. tostring(dead)
        .. ',"kills":' .. (tonumber(kills) or 0)
        .. ',"hours":' .. hours
        .. ',"weapon":' .. str(weapon)
        .. ',"bag":' .. str(bag)
        .. ',"weight":' .. weight .. ',"maxWeight":' .. maxw
        .. ',"moodles":' .. moodlesJson(p)
        .. ',"wounds":' .. woundsJson(p)
        .. ',"vehicle":' .. vehicleJson(p) .. '}'
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

-- ───────────── Pings del mapa web → juego ─────────────
local DIRS = { "E", "NE", "N", "NO", "O", "SO", "S", "SE" }

local function direction(dx, dy)
    local ny = -dy -- en el juego el norte es y negativo
    local ang
    if math.atan2 then ang = math.atan2(ny, dx)
    elseif dx == 0 then ang = (ny >= 0) and math.pi / 2 or -math.pi / 2
    else ang = math.atan(ny / dx); if dx < 0 then ang = ang + math.pi end end
    local i = math.floor((ang / (2 * math.pi)) * 8 + 0.5) % 8
    return DIRS[i + 1]
end

function PZLiveMap.showPing(args)
    local p = getPlayer and getPlayer()
    if not p or not args then return end
    local x, y = tonumber(args.x) or 0, tonumber(args.y) or 0
    local dx, dy = x - p:getX(), y - p:getY()
    local dist = math.floor(math.sqrt(dx * dx + dy * dy) + 0.5)
    local who = tostring(args.from or "Alguien")
    local msg = who .. " marcó un punto: "
    if dist < 8 then msg = msg .. "aquí cerca" else msg = msg .. dist .. " m al " .. direction(dx, dy) end
    if args.text and args.text ~= "" then msg = msg .. " (" .. tostring(args.text) .. ")" end
    local shown = pcall(function() HaloTextHelper.addText(p, msg, HaloTextHelper.getColorWhite()) end)
    if not shown then shown = pcall(function() HaloTextHelper.addText(p, msg) end) end
    if not shown then pcall(function() p:setHaloNote(msg) end) end
    pcall(function() getSoundManager():playUISound("UIActivateMainMenuItem") end)
end

-- Lee la bandeja que deja PZ Share (una línea por ping: id|x|y|de|texto) y devuelve los nuevos.
PZLiveMap.seen = PZLiveMap.seen or {}
function PZLiveMap.readInbox()
    local r = getFileReader("pzlivemap_in.txt", false)
    if not r then return {} end
    local out = {}
    local line = r:readLine()
    while line do
        local id, x, y, from, text = string.match(line, "^([^|]+)|([^|]+)|([^|]+)|([^|]*)|?(.*)$")
        if id and not PZLiveMap.seen[id] then
            PZLiveMap.seen[id] = true
            out[#out + 1] = { x = tonumber(x), y = tonumber(y), from = from, text = text }
        end
        line = r:readLine()
    end
    r:close()
    return out
end
