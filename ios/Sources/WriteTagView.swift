import SwiftUI

struct WriteTagView: View {
    @EnvironmentObject var api: SpoolBridgeAPI
    @StateObject private var nfc = NFCWriter()
    let spool: Spool

    @State private var tagData: TagResponse?
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Spool info header
                VStack(spacing: 8) {
                    Circle()
                        .fill(Color(hex: spool.colorHex))
                        .frame(width: 60, height: 60)
                        .overlay(
                            Circle().stroke(Color.primary.opacity(0.2), lineWidth: 2)
                        )
                    Text(spool.displayName)
                        .font(.title2.bold())
                    Text("\(spool.brandName) \u{00B7} \(spool.material) \u{00B7} \(spool.weightText)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.top)

                if loading {
                    ProgressView("Loading tag data...")
                } else if let error {
                    Text(error)
                        .foregroundStyle(.red)
                        .font(.caption)
                } else if let tagData {
                    // Write button
                    writeSection(tagData: tagData)

                    // JSON preview
                    jsonPreview(tagData: tagData)
                }
            }
            .padding()
        }
        .navigationTitle("Write Tag")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadTag() }
    }

    @ViewBuilder
    func writeSection(tagData: TagResponse) -> some View {
        VStack(spacing: 12) {
            switch nfc.status {
            case .idle:
                Button {
                    nfc.write(
                        jsonData: tagData.tagJson.toData(),
                        url: tagData.spoolUrl
                    )
                } label: {
                    Label("Write to NFC Tag", systemImage: "wave.3.forward")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)

                Text("Hold an NTAG215 sticker to the top of your iPhone.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)

            case .waiting:
                ProgressView()
                Text("Hold tag to iPhone...")
                    .font(.callout)
                    .foregroundStyle(.secondary)

            case .writing:
                ProgressView()
                Text("Writing...")
                    .font(.callout)

            case .success(let uid):
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.green)
                Text("Tag written!")
                    .font(.headline)
                    .foregroundStyle(.green)
                if !uid.isEmpty {
                    Text("UID: \(uid)")
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(.secondary)
                }
                Button("Write Another") {
                    nfc.status = .idle
                }
                .buttonStyle(.bordered)

            case .error(let msg):
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.red)
                Text(msg)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                Button("Try Again") {
                    nfc.status = .idle
                }
                .buttonStyle(.bordered)
            }
        }
        .padding()
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    func jsonPreview(tagData: TagResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("OpenSpool JSON")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
            Text(tagData.tagJson.toJsonString())
                .font(.caption)
                .monospaced()
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.systemGray6))
                .cornerRadius(8)
                .textSelection(.enabled)
        }
    }

    func loadTag() async {
        do {
            tagData = try await api.fetchTagData(spoolId: spool.id)
            loading = false
        } catch {
            self.error = error.localizedDescription
            loading = false
        }
    }
}
