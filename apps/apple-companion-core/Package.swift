// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LightworkConnectionKit",
    platforms: [.macOS(.v13), .watchOS(.v9), .visionOS(.v1)],
    products: [.library(name: "LightworkConnectionKit", targets: ["LightworkConnectionKit"])],
    targets: [
        .target(name: "LightworkConnectionKit"),
        .testTarget(name: "LightworkConnectionKitTests", dependencies: ["LightworkConnectionKit"])
    ]
)
