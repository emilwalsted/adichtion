cask "adichtion" do
  version "1.0.1"
  sha256 "d4c883496967d795021e37d56392e97fc0d039171fdcaf3e735f794084f9709f"

  url "https://github.com/emilwalsted/adichtion/releases/download/v#{version}/adichtion-#{version}.dmg"
  name "adichtion"
  desc "Viewer and exporter for LabChart .adicht recordings"
  homepage "https://www.respmech.dk/adichtion.html"

  livecheck do
    url :url
    strategy :github_latest
  end

  # The dmg is built on Apple silicon only (briefcase, universal_build = false).
  depends_on arch: :arm64
  depends_on macos: ">= :monterey"

  app "adichtion.app"

  zap trash: [
    "~/Library/Preferences/com.adichtion.adichtion.plist",
    "~/Library/Saved Application State/dk.emilwalsted.adichtion.savedState",
  ]
end
