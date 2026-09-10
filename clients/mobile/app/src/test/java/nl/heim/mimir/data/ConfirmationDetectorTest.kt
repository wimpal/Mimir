package nl.heim.mimir.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfirmationDetectorTest {
    @Test
    fun readOnlyQuestionsDoNotRequestWrite() {
        assertFalse(ConfirmationDetector.userMessageRequestsWrite("What's on the shopping list?"))
        assertFalse(ConfirmationDetector.userMessageRequestsWrite("what's low on stock?"))
    }

    @Test
    fun mutationPhrasesRequestWrite() {
        assertTrue(ConfirmationDetector.userMessageRequestsWrite("Add coffee to the shopping list"))
        assertTrue(ConfirmationDetector.userMessageRequestsWrite("Voeg koffie toe aan de boodschappenlijst"))
    }

    @Test
    fun showsConfirmWhenAssistantAsksAndNoToolsUsed() {
        val assistant = "Shall I add coffee to the shopping list?"
        assertTrue(
            ConfirmationDetector.shouldShowWriteConfirm(
                assistantText = assistant,
                toolsUsed = emptyList(),
                priorUserMessage = "Add coffee to the shopping list",
            ),
        )
    }

    @Test
    fun hidesConfirmWhenToolsAlreadyUsed() {
        assertFalse(
            ConfirmationDetector.shouldShowWriteConfirm(
                assistantText = "Done — coffee is on the list.",
                toolsUsed = listOf("homebase.shopping_list.add_item"),
                priorUserMessage = "yes",
            ),
        )
    }

    @Test
    fun dutchConfirmReply() {
        assertTrue(ConfirmationDetector.confirmReply("Zal ik koffie toevoegen?") == "ja")
        assertTrue(ConfirmationDetector.confirmReply("Shall I add coffee?") == "yes")
    }

    @Test
    fun spokenConfirmAndCancel() {
        assertTrue(ConfirmationDetector.isSpokenConfirm("ja"))
        assertTrue(ConfirmationDetector.isSpokenConfirm("yes"))
        assertTrue(ConfirmationDetector.isSpokenCancel("nee"))
        assertTrue(ConfirmationDetector.isSpokenCancel("no"))
        assertFalse(ConfirmationDetector.isSpokenConfirm("wat staat er op de lijst"))
    }
}
