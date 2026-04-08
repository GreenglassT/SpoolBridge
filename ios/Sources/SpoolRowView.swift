import SwiftUI

struct SpoolRowView: View {
    let spool: Spool

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(Color(hex: spool.colorHex))
                .frame(width: 28, height: 28)
                .overlay(
                    Circle().stroke(Color.primary.opacity(0.2), lineWidth: 1)
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(spool.displayName)
                    .font(.body)
                Text("\(spool.brandName) \u{00B7} \(spool.material)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(spool.weightText)
                    .font(.caption)
                    .monospacedDigit()
                if spool.hasTag {
                    Label("NFC", systemImage: "wave.3.right")
                        .font(.caption2)
                        .foregroundStyle(.green)
                }
            }
        }
        .padding(.vertical, 2)
    }
}

// MARK: - Color from hex

extension Color {
    init(hex: String) {
        let h = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var rgb: UInt64 = 0
        Scanner(string: h).scanHexInt64(&rgb)
        self.init(
            red: Double((rgb >> 16) & 0xFF) / 255,
            green: Double((rgb >> 8) & 0xFF) / 255,
            blue: Double(rgb & 0xFF) / 255
        )
    }
}
