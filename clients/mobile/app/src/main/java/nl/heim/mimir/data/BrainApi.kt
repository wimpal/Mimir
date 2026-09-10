package nl.heim.mimir.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import nl.heim.mimir.model.ConversationSummary
import nl.heim.mimir.model.HealthInfo
import nl.heim.mimir.model.HealthState
import nl.heim.mimir.model.SttResult
import nl.heim.mimir.model.SseEvent
import nl.heim.mimir.model.StoredMessage
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.TimeUnit

/**
 * Port of [clients.tui.brain_client.BrainClient].
 */
class BrainApi(
    private val baseUrl: String,
    private val authToken: String,
) {
    companion object {
        const val CONNECT_TIMEOUT_S = 5L
        const val TURN_TIMEOUT_S = 180L
        const val CONTROL_TIMEOUT_S = 10L
        const val CONVERSATIONS_LIST_DEFAULT = 50
        const val VOICE_STT_TIMEOUT_S = 90L
        const val VOICE_TTS_TIMEOUT_S = 30L

        fun normalizeBrainUrl(url: String): String {
            val text = url.trim()
            require(text.isNotEmpty()) { "brain URL is empty" }
            val uri = URI(text)
            require(uri.scheme != null && uri.host != null) { "invalid brain URL: $url" }
            var path = (uri.path ?: "").trimEnd('/')
            if (path == "/v1" || path.endsWith("/v1")) {
                path = if (path.endsWith("/v1")) path.dropLast(3) else ""
            }
            val cleaned = URI(
                uri.scheme,
                uri.userInfo,
                uri.host,
                uri.port,
                path.ifEmpty { "" },
                null,
                null,
            ).toString().trimEnd('/')
            return cleaned
        }
    }

    private val normalizedBase = normalizeBrainUrl(baseUrl)

    private val controlClient = buildClient(CONTROL_TIMEOUT_S)
    private val chatClient = buildClient(TURN_TIMEOUT_S)
    private val voiceClient = buildClient(VOICE_STT_TIMEOUT_S)
    private val ttsClient = buildClient(VOICE_TTS_TIMEOUT_S)

    private fun buildClient(readTimeoutS: Long): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            .readTimeout(readTimeoutS, TimeUnit.SECONDS)
            .writeTimeout(CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            .callTimeout(readTimeoutS + CONNECT_TIMEOUT_S, TimeUnit.SECONDS)
            .build()
    }

    private fun authRequestBuilder(path: String): Request.Builder {
        val builder = Request.Builder()
            .url("$normalizedBase$path")
            .header("Accept", "application/json")
        if (authToken.isNotBlank()) {
            builder.header("Authorization", "Bearer $authToken")
        }
        return builder
    }

    suspend fun health(): HealthInfo = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$normalizedBase/health")
            .header("Accept", "application/json")
            .get()
            .build()
        try {
            controlClient.newCall(request).execute().use { response ->
                val bodyText = response.body?.string().orEmpty()
                if (!response.isSuccessful && response.code >= 500) {
                    return@withContext HealthInfo(
                        state = HealthState.Offline,
                        status = "fail",
                        detail = "HTTP ${response.code}",
                    )
                }
                val body = try {
                    JSONObject(bodyText)
                } catch (_: Exception) {
                    throw BrainClientError("health returned non-JSON")
                }
                val status = body.optString("status", "unknown")
                var detail = ""
                val ollama = body.optJSONObject("ollama")
                if (ollama != null && ollama.optBoolean("reachable", true) == false) {
                    detail = "ollama unreachable"
                } else if (status != "ok") {
                    detail = "status=$status"
                }
                val state = when {
                    !response.isSuccessful -> HealthState.Offline
                    status == "ok" -> HealthState.Ok
                    else -> HealthState.Degraded
                }
                HealthInfo(state = state, status = status, detail = detail)
            }
        } catch (e: java.net.SocketTimeoutException) {
            throw BrainClientError("brain health timed out at $normalizedBase", e)
        } catch (e: java.io.IOException) {
            throw BrainClientError("brain unreachable at $normalizedBase: ${e.message}", e)
        }
    }

    suspend fun listConversations(limit: Int = CONVERSATIONS_LIST_DEFAULT): List<ConversationSummary> =
        withContext(Dispatchers.IO) {
            val request = authRequestBuilder("/v1/conversations?limit=$limit").get().build()
            executeJson(request, "list conversations").let { body ->
                val rows = body.optJSONArray("conversations") ?: JSONArray()
                buildList {
                    for (i in 0 until rows.length()) {
                        val item = rows.optJSONObject(i) ?: continue
                        val id = item.optString("id", "").trim()
                        if (id.isEmpty()) continue
                        add(
                            ConversationSummary(
                                id = id,
                                createdAt = item.optString("created_at", ""),
                                updatedAt = item.optString("updated_at", ""),
                                preview = item.optString("preview", ""),
                                messageCount = item.optInt("message_count", 0),
                            ),
                        )
                    }
                }
            }
        }

    suspend fun listMessages(conversationId: String): List<StoredMessage> =
        withContext(Dispatchers.IO) {
            val cid = conversationId.trim()
            if (cid.isEmpty()) return@withContext emptyList()
            val request = authRequestBuilder("/v1/conversations/$cid/messages").get().build()
            executeJson(request, "list messages").let { body ->
                val rows = body.optJSONArray("messages") ?: JSONArray()
                buildList {
                    for (i in 0 until rows.length()) {
                        val item = rows.optJSONObject(i) ?: continue
                        add(
                            StoredMessage(
                                role = item.optString("role", ""),
                                content = item.optString("content", ""),
                                createdAt = item.optString("created_at").ifEmpty { null },
                            ),
                        )
                    }
                }
            }
        }

    fun streamChat(message: String, conversationId: String?): Flow<SseEvent> = flow {
        val payload = JSONObject().apply {
            put("message", message)
            put("stream", true)
            if (!conversationId.isNullOrBlank()) {
                put("conversation_id", conversationId.trim())
            }
        }
        val body = payload.toString().toRequestBody("application/json".toMediaType())
        val request = authRequestBuilder("/v1/chat")
            .header("Accept", "text/event-stream")
            .post(body)
            .build()

        try {
            chatClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val raw = response.body?.string().orEmpty()
                    val detail = parseErrorDetail(raw) ?: raw.ifEmpty { "HTTP ${response.code}" }
                    if (response.code == 401) {
                        throw BrainClientError("Bad token — check Settings.")
                    }
                    throw BrainClientError(detail)
                }
                val body = response.body
                    ?: throw BrainClientError("chat failed: empty response body")
                var buffer = ""
                body.byteStream().bufferedReader().use { reader ->
                    var line: String?
                    while (reader.readLine().also { line = it } != null) {
                        buffer += line + "\n"
                        val (events, remainder) = SseParser.parseChunk(buffer)
                        buffer = remainder
                        for (eventJson in events) {
                            parseSseEvent(eventJson)?.let { emit(it) }
                        }
                    }
                }
                for (eventJson in SseParser.flushRemainder(buffer)) {
                    parseSseEvent(eventJson)?.let { emit(it) }
                }
            }
        } catch (e: BrainClientError) {
            throw e
        } catch (e: java.net.SocketTimeoutException) {
            throw BrainClientError("Chat timed out after ${TURN_TIMEOUT_S}s.", e)
        } catch (e: java.io.IOException) {
            throw BrainClientError("Brain unreachable at $normalizedBase.", e)
        }
    }.flowOn(Dispatchers.IO)

    suspend fun stt(audio: ByteArray, language: String? = null): SttResult =
        withContext(Dispatchers.IO) {
            val path = buildString {
                append("/v1/stt")
                if (!language.isNullOrBlank()) {
                    append("?language=${language.trim()}")
                }
            }
            val request = authRequestBuilder(path)
                .header("Content-Type", "audio/wav")
                .post(audio.toRequestBody("audio/wav".toMediaType()))
                .build()
            try {
                voiceClient.newCall(request).execute().use { response ->
                    val raw = response.body?.string().orEmpty()
                    if (response.code == 401) {
                        throw BrainClientError("Bad token — check Settings.")
                    }
                    if (!response.isSuccessful) {
                        throw BrainClientError(parseVoiceError(raw) ?: raw.take(200))
                    }
                    val body = JSONObject(raw)
                    val text = body.optString("text", "").trim()
                    if (text.isEmpty()) {
                        throw BrainClientError("No speech detected.")
                    }
                    val lang = body.optString("language").ifEmpty { null }
                    SttResult(text = text, language = lang)
                }
            } catch (e: BrainClientError) {
                throw e
            } catch (e: java.net.SocketTimeoutException) {
                throw BrainClientError("Speech recognition timed out.", e)
            } catch (e: java.io.IOException) {
                throw BrainClientError("Speech recognition failed: ${e.message}", e)
            }
        }

    suspend fun tts(text: String, locale: String = "nl"): ByteArray =
        withContext(Dispatchers.IO) {
            val payload = JSONObject().apply {
                put("text", text)
                put("locale", locale)
            }
            val body = payload.toString().toRequestBody("application/json".toMediaType())
            val request = authRequestBuilder("/v1/tts")
                .header("Accept", "audio/wav")
                .post(body)
                .build()
            try {
                ttsClient.newCall(request).execute().use { response ->
                    if (response.code == 401) {
                        throw BrainClientError("Bad token — check Settings.")
                    }
                    if (!response.isSuccessful) {
                        val raw = response.body?.string().orEmpty()
                        throw BrainClientError(parseVoiceError(raw) ?: raw.take(200))
                    }
                    response.body?.bytes()
                        ?: throw BrainClientError("Speech synthesis returned empty audio.")
                }
            } catch (e: BrainClientError) {
                throw e
            } catch (e: java.net.SocketTimeoutException) {
                throw BrainClientError("Speech synthesis timed out.", e)
            } catch (e: java.io.IOException) {
                throw BrainClientError("Speech synthesis failed: ${e.message}", e)
            }
        }

    private fun parseVoiceError(raw: String): String? {
        return try {
            val err = JSONObject(raw)
            val nested = err.optJSONObject("error")
            if (nested != null) {
                nested.optString("message").ifEmpty { null }
            } else {
                err.optString("detail").ifEmpty { null }
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun executeJson(request: Request, label: String): JSONObject {
        try {
            controlClient.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (response.code == 401) {
                    throw BrainClientError("Bad token — check Settings.")
                }
                if (!response.isSuccessful) {
                    val detail = parseErrorDetail(raw) ?: raw.take(200)
                    throw BrainClientError("$label HTTP ${response.code}: $detail")
                }
                return try {
                    JSONObject(raw)
                } catch (_: Exception) {
                    throw BrainClientError("$label returned non-JSON")
                }
            }
        } catch (e: BrainClientError) {
            throw e
        } catch (e: java.net.SocketTimeoutException) {
            throw BrainClientError("$label timed out", e)
        } catch (e: java.io.IOException) {
            throw BrainClientError("$label failed: ${e.message}", e)
        }
    }

    private fun parseErrorDetail(raw: String): String? {
        return try {
            val err = JSONObject(raw)
            err.optString("detail").ifEmpty { null }
        } catch (_: Exception) {
            null
        }
    }

    private fun parseSseEvent(json: JSONObject): SseEvent? {
        return when (json.optString("type")) {
            "meta" -> SseEvent.Meta(
                conversationId = json.optString("conversation_id").ifEmpty { null },
                locale = json.optString("locale").ifEmpty { null },
            )
            "token" -> {
                val text = json.optString("text")
                if (text.isNotEmpty()) SseEvent.Token(text) else null
            }
            "sentence" -> {
                val text = json.optString("text")
                if (text.isNotEmpty()) {
                    SseEvent.Sentence(
                        index = json.optInt("index", 0),
                        text = text,
                    )
                } else {
                    null
                }
            }
            "tool_start" -> SseEvent.ToolStart(json.optString("name", "tool"))
            "tool_end" -> SseEvent.ToolEnd
            "done" -> {
                val tools = json.optJSONArray("tools_used")
                val names = buildList {
                    if (tools != null) {
                        for (i in 0 until tools.length()) {
                            val name = tools.optString(i).trim()
                            if (name.isNotEmpty()) add(name)
                        }
                    }
                }
                SseEvent.Done(
                    conversationId = json.optString("conversation_id").ifEmpty { null },
                    toolsUsed = names,
                    stoppedReason = json.optString("stopped_reason").ifEmpty { null },
                )
            }
            "error" -> SseEvent.ErrorEvent(
                message = json.optString("message", "Something went wrong."),
                conversationId = json.optString("conversation_id").ifEmpty { null },
            )
            else -> SseEvent.Unknown(json.optString("type"))
        }
    }
}
