import Foundation

class SpoolBridgeAPI: ObservableObject {
    // TODO: Change this to your SpoolBridge server IP
    static let defaultBaseURL = "http://192.168.50.80:5555"

    @Published var baseURL: String {
        didSet { UserDefaults.standard.set(baseURL, forKey: "serverURL") }
    }

    init() {
        self.baseURL = UserDefaults.standard.string(forKey: "serverURL")
            ?? Self.defaultBaseURL
    }

    func fetchSpools() async throws -> [Spool] {
        let url = URL(string: "\(baseURL)/api/spools")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode([Spool].self, from: data)
    }

    func fetchTagData(spoolId: Int) async throws -> TagResponse {
        let url = URL(string: "\(baseURL)/api/tag/\(spoolId)")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(TagResponse.self, from: data)
    }
}
