package nl.heim.mimir.data

class BrainClientError(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
