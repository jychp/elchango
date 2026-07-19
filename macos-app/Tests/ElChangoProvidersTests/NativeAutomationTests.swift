import Testing

@testable import ElChangoProviders

@Suite("Native input verification")
struct NativeAutomationTests {
    @Test("character count is authoritative over placeholder value")
    func characterCountIsAuthoritative() {
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: 0,
                value: "Localized contextual placeholder"
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: 4,
                value: ""
            )
        )
    }

    @Test("value fallback accepts only empty editor representations")
    func valueFallback() {
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: ""
            )
        )
        #expect(
            NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "\n"
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: "Draft"
            )
        )
        #expect(
            !NativeAutomation.inputIsEmpty(
                characterCount: nil,
                value: nil
            )
        )
    }
}
