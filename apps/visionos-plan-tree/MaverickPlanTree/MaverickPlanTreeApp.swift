import SwiftUI

@main
struct MaverickPlanTreeApp: App {
    var body: some Scene {
        WindowGroup { PlanTreeVolume() }
            .windowStyle(.volumetric)
            .defaultSize(width: 1.2, height: 0.8, depth: 0.6, in: .meters)
    }
}
