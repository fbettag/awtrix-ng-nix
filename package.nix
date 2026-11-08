{
  lib,
  buildPythonPackage,
  setuptools,
}:
buildPythonPackage {
  pname = "awtrix-ng-nix";
  version = "0.1.0";
  src = ./.;
  pyproject = true;
  build-system = [ setuptools ];
  checkPhase = ''
    python -m unittest discover -s tests -v
  '';
  pythonImportsCheck = [ "awtrix_ng_nix" ];
  meta = {
    description = "Declarative AWTRIX NG management with identity checks and verified read-back";
    homepage = "https://github.com/fbettag/awtrix-ng-nix";
    license = lib.licenses.mit;
    mainProgram = "awtrix-ng-nix";
  };
}
