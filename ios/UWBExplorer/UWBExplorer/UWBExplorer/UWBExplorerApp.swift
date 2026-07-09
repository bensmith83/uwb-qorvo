import SwiftUI

@main
struct UWBExplorerApp: App {
    @StateObject private var ble = BLEManager()

    var body: some Scene {
        WindowGroup {
            TabView {
                ContentView()
                    .tabItem { Label("Live", systemImage: "dot.radiowaves.left.and.right") }
                HistoryView()
                    .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }
                LearnView()
                    .tabItem { Label("Learn", systemImage: "book") }
                ExperimentsView()
                    .tabItem { Label("Experiments", systemImage: "flask") }
            }
            .environmentObject(ble)
        }
    }
}
