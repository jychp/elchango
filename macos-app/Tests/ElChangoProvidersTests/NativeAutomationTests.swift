import Testing

@testable import ElChangoProviders

@Suite("Native input verification")
struct NativeAutomationTests {
    @Test("draft capture preserves localized text")
    func draftCapture() {
        #expect(
            NativeAutomation.boundedDraft(
                "Brouillon localisé"
            ) == "Brouillon localisé"
        )
        #expect(
            NativeAutomation.boundedDraft("") == ""
        )
    }

    @Test("draft capture rejects missing and oversized values")
    func draftCaptureBounds() {
        #expect(
            NativeAutomation.boundedDraft(nil) == nil
        )
        #expect(
            NativeAutomation.boundedDraft(
                "12345",
                maximumBytes: 4
            ) == nil
        )
    }

    @Test("command text must replace the previous draft before submission")
    func commandReplacementVerification() {
        #expect(
            NativeAutomation.inputTextMatches(
                "Open a pull request.",
                expected: "Open a pull request."
            )
        )
        #expect(
            NativeAutomation.inputTextMatches(
                "Open a pull request.\n",
                expected: "Open a pull request."
            )
        )
        #expect(
            !NativeAutomation.inputTextMatches(
                "Existing test draft\n",
                expected: "Open a pull request."
            )
        )
    }
}
