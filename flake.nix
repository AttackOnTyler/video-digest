{
  description = "video-digest: YouTube video/playlist -> Markdown of frames + transcript";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      # A stray LD_LIBRARY_PATH (e.g. pointing at another glibc's alsa-lib) breaks
      # Nix's ffmpeg; Nix binaries never need it, so both entry points clear it.
      packages = forAll (pkgs: {
        default = pkgs.writeShellApplication {
          name = "video-digest";
          runtimeInputs = [ pkgs.python3 pkgs.yt-dlp pkgs.ffmpeg pkgs.deno ];
          text = ''
            unset LD_LIBRARY_PATH
            exec python3 ${./video_digest.py} "$@"
          '';
        };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.yt-dlp pkgs.ffmpeg pkgs.deno ];
          shellHook = ''
            unset LD_LIBRARY_PATH
            export PATH="$PWD:$PATH"
          '';
        };
      });
    };
}
