import SwiftUI

@main
struct SpoolBridgeApp: App {
    @StateObject private var api = SpoolBridgeAPI()

    var body: some Scene {
        WindowGroup {
            SpoolListView()
                .environmentObject(api)
        }
    }
}
