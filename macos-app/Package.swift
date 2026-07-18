// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "elChango",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "ElChangoCore", targets: ["ElChangoCore"]),
        .library(name: "ElChangoProviders", targets: ["ElChangoProviders"]),
        .executable(name: "ElChangoApp", targets: ["ElChangoApp"]),
    ],
    dependencies: [
        .package(
            url: "https://github.com/swhitty/FlyingFox.git",
            exact: "0.26.2"
        ),
    ],
    targets: [
        .target(
            name: "ElChangoCore",
            dependencies: [
                .product(name: "FlyingFox", package: "FlyingFox"),
                .product(name: "FlyingSocks", package: "FlyingFox"),
            ],
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
            ]
        ),
        .target(
            name: "ElChangoProviders",
            dependencies: ["ElChangoCore"],
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
            ]
        ),
        .executableTarget(
            name: "ElChangoApp",
            dependencies: ["ElChangoCore", "ElChangoProviders"],
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
            ]
        ),
        .testTarget(
            name: "ElChangoCoreTests",
            dependencies: [
                "ElChangoCore",
                .product(name: "FlyingFox", package: "FlyingFox"),
            ],
            swiftSettings: [
                .enableUpcomingFeature("StrictConcurrency"),
                .enableExperimentalFeature("SwiftTesting"),
            ]
        ),
    ]
)
