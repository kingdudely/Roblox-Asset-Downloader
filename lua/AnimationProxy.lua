--!nonstrict
-- AnimationProxy (ServerScriptService ModuleScript)
--
-- Turns a third-party animation into a playable asset id through the relay.
-- The game server cannot call Roblox asset delivery itself: HttpService blocks
-- the headers and contentdelivery, and RegisterAnimationClip is Studio-only.
--
--   local AnimationProxy = require(game.ServerScriptService.AnimationProxy)
--   AnimationProxy.RelayUrl = "https://relay.example.com"
--   AnimationProxy.RelayToken = "shared-secret"
--
--   local track = AnimationProxy.play(humanoid, 10449761463, 77727115892579)
--   if track then track:Play() end
--
-- Warm the cache at startup with prefetch(): the relay's first call uploads and
-- can take longer than one HttpService request.

local HttpService = game:GetService("HttpService")

local AnimationProxy = {}
AnimationProxy.RelayUrl = "http://127.0.0.1:8080"
AnimationProxy.RelayToken = ""

local cache: { [string]: number } = {}

local function post(url: string, payload: any): (any?, string?)
	local headers = { ["Content-Type"] = "application/json" }
	if AnimationProxy.RelayToken ~= "" then
		headers.Authorization = "Bearer " .. AnimationProxy.RelayToken
	end
	local ok, res = pcall(HttpService.RequestAsync, HttpService, {
		Url = url,
		Method = "POST",
		Headers = headers,
		Body = HttpService:JSONEncode(payload),
	})
	if not ok then
		return nil, tostring(res)
	end
	if res.StatusCode ~= 200 then
		return nil, string.format("relay %d: %s", res.StatusCode, res.Body)
	end
	local decOk, data = pcall(HttpService.JSONDecode, HttpService, res.Body)
	return decOk and data or nil, decOk and nil or "bad JSON"
end

function AnimationProxy.resolve(placeId: number, animationId: number): (number?, string?)
	local key = animationId .. ":" .. placeId
	if cache[key] then
		return cache[key]
	end
	local data, err = post(AnimationProxy.RelayUrl .. "/api/resolve", {
		placeId = placeId,
		animationId = animationId,
	})
	if not data then
		return nil, err
	end
	if not data.assetId then
		return nil, data.error or "relay returned no assetId"
	end
	cache[key] = data.assetId
	return data.assetId
end

function AnimationProxy.prefetch(placeId: number, animationId: number)
	task.spawn(AnimationProxy.resolve, placeId, animationId)
end

function AnimationProxy.play(humanoid: Humanoid, placeId: number, animationId: number)
	local assetId, err = AnimationProxy.resolve(placeId, animationId)
	if not assetId then
		return nil, err
	end
	local animator = humanoid:FindFirstChildOfClass("Animator") or Instance.new("Animator", humanoid)
	local anim = Instance.new("Animation")
	anim.AnimationId = "rbxassetid://" .. assetId
	local ok, track = pcall(function()
		return animator:LoadAnimation(anim)
	end)
	if not ok then
		anim:Destroy()
		return nil, tostring(track)
	end
	return track
end

return AnimationProxy
