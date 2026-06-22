cask "vivotype" do
  version "0.1.0"
  sha256 "PLACEHOLDER"

  url "https://github.com/kalpitt/VivoType/releases/download/v#{version}/VivoType-v#{version}.zip"
  name "VivoType"
  desc "Fully local, privacy-first macOS dictation app"
  homepage "https://github.com/kalpitt/VivoType"

  depends_on macos: ">= :ventura"
  depends_on arch: :arm64

  app "VivoType.app"

  zap trash: [
    "~/Library/Application Support/VivoType",
    "~/Library/Preferences/com.vivotype.app.plist",
  ]
end
