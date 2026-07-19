import Testing

@testable import ElChangoProviders

@Suite("Native input verification")
struct NativeAutomationTests {
    @Test("full input value capture preserves localized and multiline drafts")
    func draftCapture() {
        #expect(
            NativeAutomation.boundedDraft(
                "Brouillon localisé\nDeuxième ligne"
            ) == "Brouillon localisé\nDeuxième ligne"
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

    @Test("dynamic placeholders are not captured as drafts")
    func placeholderNormalization() {
        #expect(
            NativeAutomation.normalizedInputText(
                value: "15 characters",
                characterCount: 0
            ) == ""
        )
        #expect(
            NativeAutomation.normalizedInputText(
                value: "Existing draft",
                characterCount: 14
            ) == "Existing draft"
        )
        #expect(
            NativeAutomation.normalizedInputText(
                value: "Fallback value",
                characterCount: nil
            ) == "Fallback value"
        )
        #expect(
            NativeAutomation.normalizedInputText(
                value: "Invalid",
                characterCount: -1
            ) == nil
        )
    }
}
