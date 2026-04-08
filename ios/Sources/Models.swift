import Foundation

struct Spool: Codable, Identifiable {
    let id: Int
    let filament: Filament?
    let remainingWeight: Double?
    let initialWeight: Double?
    let location: String?
    let archived: Bool?
    let extra: SpoolExtra?

    enum CodingKeys: String, CodingKey {
        case id, filament, location, archived, extra
        case remainingWeight = "remaining_weight"
        case initialWeight = "initial_weight"
    }

    var displayName: String {
        filament?.name ?? "Unknown"
    }

    var material: String {
        filament?.material ?? "?"
    }

    var colorHex: String {
        filament?.colorHex ?? "888888"
    }

    var brandName: String {
        filament?.vendor?.name ?? "Unknown"
    }

    var isBambu: Bool {
        brandName.lowercased().contains("bambu")
    }

    var hasTag: Bool {
        guard let tag = extra?.tag else { return false }
        let cleaned = tag.replacingOccurrences(of: "\"", with: "").trimmingCharacters(in: .whitespaces)
        return !cleaned.isEmpty
    }

    var weightText: String {
        let w = remainingWeight ?? 0
        return "\(Int(w))g"
    }

    var percentRemaining: Double {
        let total = filament?.weight ?? 1000
        guard total > 0 else { return 0 }
        return (remainingWeight ?? 0) / total
    }
}

struct Filament: Codable {
    let id: Int
    let name: String?
    let material: String?
    let colorHex: String?
    let weight: Double?
    let vendor: Vendor?

    enum CodingKeys: String, CodingKey {
        case id, name, material, weight, vendor
        case colorHex = "color_hex"
    }
}

struct Vendor: Codable {
    let id: Int
    let name: String?
}

struct SpoolExtra: Codable {
    let tag: String?
    let activeTray: String?

    enum CodingKeys: String, CodingKey {
        case tag
        case activeTray = "active_tray"
    }
}

struct TagResponse: Codable {
    let tagJson: TagData
    let spoolUrl: String
    let spool: Spool

    enum CodingKeys: String, CodingKey {
        case spool
        case tagJson = "tag_json"
        case spoolUrl = "spool_url"
    }
}

struct TagData: Codable {
    let version: String?
    let protocol: String?
    let colorHex: String?
    let type: String?
    let minTemp: Int?
    let maxTemp: Int?
    let brand: String?

    enum CodingKeys: String, CodingKey {
        case version, protocol, type, brand
        case colorHex = "color_hex"
        case minTemp = "min_temp"
        case maxTemp = "max_temp"
    }

    func toJsonString() -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return "{}" }
        return String(data: data, encoding: .utf8) ?? "{}"
    }

    func toData() -> Data {
        let encoder = JSONEncoder()
        return (try? encoder.encode(self)) ?? Data()
    }
}
