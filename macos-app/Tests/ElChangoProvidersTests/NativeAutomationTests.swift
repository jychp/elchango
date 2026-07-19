import Testing

@testable import ElChangoProviders

@Suite("Native input verification")
struct NativeAutomationTests {
    @Test("character count is authoritative over placeholder value")
    func characterCountIsAuthoritative() {
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: 0,
                value: "Localized contextual placeholder",
                placeholderValue: nil
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: 4,
                value: "",
                placeholderValue: ""
            )
        )
    }

    @Test("value fallback accepts only empty editor representations")
    func valueFallback() {
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "",
                placeholderValue: nil
            )
        )
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "\n",
                placeholderValue: nil
            )
        )
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "Localized contextual placeholder",
                placeholderValue: "Localized contextual placeholder"
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "Draft",
                placeholderValue: "Localized contextual placeholder"
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: nil,
                placeholderValue: nil
            )
        )
    }
}
