{
  description = "polymorph: generate coding-harness hook configs from one canonical agent config";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "polymorph";
          version = "0.0.1.dev0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          # stdlib only; the suite runs as checks.default instead
          doCheck = false;
          # mapping.py and conformance.py find mappings/ and fixtures/ at
          # <site-packages>/../ (parents[2] of the module), the source-tree layout.
          postInstall = ''
            dest=$out/${pkgs.python3.sitePackages}/..
            cp -r mappings fixtures "$dest"/
          '';
          meta = {
            description = "Generate coding-harness hook configs from one canonical agent config";
            license = pkgs.lib.licenses.asl20;
            mainProgram = "polymorph";
          };
        };
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/polymorph";
        };
      });

      checks = forAll (pkgs: {
        default = pkgs.runCommand "polymorph-tests"
          { nativeBuildInputs = [ pkgs.python3 ]; } ''
          cp -r ${./.} src-copy
          chmod -R u+w src-copy
          cd src-copy
          PYTHONPATH=src python3 -m unittest discover -s tests
          touch $out
        '';
      });
    };
}
