import Foundation
import UIKit

/// Tells the person when a newer build exists, and takes them to it.
///
/// THIS DELIBERATELY DOES LESS THAN THE ANDROID `Updater`, BECAUSE iOS DOES
/// NOT ALLOW MORE. The Android version downloads the APK and hands it to the
/// system installer through `Intent.ACTION_VIEW`. iOS has no equivalent: there
/// is no public API by which an app can install an app, and the one mechanism
/// that comes close -- an `itms-services://` manifest -- refuses anything that
/// is not signed with a distribution certificate. This build is unsigned on
/// purpose (no $99/yr Developer Program), so that route is closed, and would
/// still be closed if this code tried it.
///
/// So the honest scope is: notice, say so, and hand off. The download page
/// explains the Sideloadly step; the app cannot take it for them.
///
/// THE REAL ONE-TAP PATH IS ALTSTORE, and it is not this file. The release
/// publishes an AltStore source alongside the IPA, so anyone who added that
/// source gets the update offered and installed by AltStore itself, which can
/// re-sign with their own Apple ID. This checker still earns its place: it is
/// what somebody who sideloaded once with Sideloadly and never set up a source
/// will actually see.
///
/// A FAILED CHECK IS SILENT, same rule as Android. The app works; it simply
/// does not know whether something newer exists, and saying so would be noise
/// on every launch behind a captive portal.
@MainActor
final class UpdateChecker: ObservableObject {

    struct Update: Equatable {
        let versionName: String
        let build: Int
        let notes: String
    }

    @Published private(set) var available: Update?

    private static let manifest = URL(string: "https://example.org/apps/ios.json")!
    private static let page = URL(string: "https://example.org/apps/")!

    /// The build this binary was compiled as. `CFBundleVersion` is the
    /// monotonic one -- `CFBundleShortVersionString` is the marketing string
    /// and is free to stay "1.0" across a dozen releases, so comparing on it
    /// would miss every one of them.
    private var currentBuild: Int {
        Int(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "") ?? 0
    }

    func check() async {
        var request = URLRequest(url: Self.manifest)
        // no-store rather than no-cache: a stale manifest hides a release,
        // and a few hundred bytes per launch is not worth caching.
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.timeoutInterval = 15

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode),
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let build = json["build"] as? Int,
                  build > currentBuild
            else {
                available = nil
                return
            }
            available = Update(
                versionName: json["versionName"] as? String ?? "",
                build: build,
                notes: json["notes"] as? String ?? ""
            )
        } catch {
            available = nil
        }
    }

    func openDownloadPage() {
        UIApplication.shared.open(Self.page)
    }
}
