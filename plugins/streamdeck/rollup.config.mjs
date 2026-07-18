import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";
import typescript from "@rollup/plugin-typescript";
import path from "node:path";
import url from "node:url";

const isWatching = Boolean(process.env.ROLLUP_WATCH);
const pluginDirectory = "com.jychp.elchango.sdPlugin";

/** @type {import("rollup").RollupOptions} */
export default {
  input: "src/plugin.ts",
  output: {
    file: `${pluginDirectory}/bin/plugin.js`,
    sourcemap: isWatching,
    sourcemapPathTransform(relativeSourcePath, sourcemapPath) {
      return url.pathToFileURL(
        path.resolve(path.dirname(sourcemapPath), relativeSourcePath),
      ).href;
    },
  },
  plugins: [
    {
      name: "watch-manifest",
      buildStart() {
        this.addWatchFile(`${pluginDirectory}/manifest.json`);
      },
    },
    typescript({
      mapRoot: isWatching ? "./" : undefined,
    }),
    nodeResolve({
      browser: false,
      exportConditions: ["node"],
      preferBuiltins: true,
    }),
    commonjs(),
    !isWatching && terser(),
    {
      name: "emit-module-package-file",
      generateBundle() {
        this.emitFile({
          fileName: "package.json",
          source: '{ "type": "module" }',
          type: "asset",
        });
      },
    },
  ],
};
