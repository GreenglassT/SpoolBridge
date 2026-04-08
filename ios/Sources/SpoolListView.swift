import SwiftUI

struct SpoolListView: View {
    @EnvironmentObject var api: SpoolBridgeAPI
    @State private var spools: [Spool] = []
    @State private var loading = true
    @State private var error: String?
    @State private var showSettings = false
    @State private var filterBambu = true

    var filteredSpools: [Spool] {
        spools.filter { s in
            if s.archived == true { return false }
            if filterBambu && s.isBambu { return false }
            return true
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    ProgressView("Loading spools...")
                } else if let error {
                    VStack(spacing: 12) {
                        Image(systemName: "wifi.slash")
                            .font(.largeTitle)
                            .foregroundStyle(.secondary)
                        Text("Can't reach SpoolBridge")
                            .font(.headline)
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                        Button("Retry") { Task { await load() } }
                            .buttonStyle(.borderedProminent)
                        Button("Settings") { showSettings = true }
                            .buttonStyle(.bordered)
                    }
                    .padding()
                } else if filteredSpools.isEmpty {
                    ContentUnavailableView(
                        "No Spools",
                        systemImage: "circle.dashed",
                        description: Text(filterBambu ? "No third-party spools found. Bambu spools already have RFID." : "No spools in Spoolman.")
                    )
                } else {
                    List(filteredSpools) { spool in
                        NavigationLink(value: spool.id) {
                            SpoolRowView(spool: spool)
                        }
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Write NFC Tag")
            .navigationDestination(for: Int.self) { spoolId in
                if let spool = spools.first(where: { $0.id == spoolId }) {
                    WriteTagView(spool: spool)
                        .environmentObject(api)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Toggle(isOn: $filterBambu) {
                        Text("Hide Bambu")
                    }
                    .toggleStyle(.switch)
                    .labelsHidden()
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsSheet(serverURL: $api.baseURL)
                    .onDisappear { Task { await load() } }
            }
        }
        .task { await load() }
    }

    func load() async {
        loading = true
        error = nil
        do {
            spools = try await api.fetchSpools()
            loading = false
        } catch {
            self.error = error.localizedDescription
            loading = false
        }
    }
}

struct SettingsSheet: View {
    @Binding var serverURL: String
    @Environment(\.dismiss) var dismiss
    @State private var draft = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("SpoolBridge Server") {
                    TextField("http://192.168.x.x:5555", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }
                Section {
                    Text("Enter the URL of your SpoolBridge web server on your local network.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        serverURL = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                        dismiss()
                    }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .onAppear { draft = serverURL }
        }
    }
}
