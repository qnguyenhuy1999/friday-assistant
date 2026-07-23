type SemVer = {
  readonly major: number;
  readonly minor: number;
  readonly patch: number;
};

function parseSemVer(version: string): SemVer {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version);
  if (!match) {
    throw new Error(`invalid semantic version: ${version}`);
  }
  const [, major, minor, patch] = match;
  return { major: Number(major), minor: Number(minor), patch: Number(patch) };
}

function isAtLeast(version: SemVer, minimum: SemVer): boolean {
  if (version.major !== minimum.major) return version.major > minimum.major;
  if (version.minor !== minimum.minor) return version.minor > minimum.minor;
  return version.patch >= minimum.patch;
}

const declaredNodeVersion = parseSemVer("22.23.1");
const minimumSupportedNode = parseSemVer("20.19.0");

if (!isAtLeast(declaredNodeVersion, minimumSupportedNode)) {
  throw new Error(
    "toolchain smoke check failed: declared Node version below supported minimum",
  );
}
