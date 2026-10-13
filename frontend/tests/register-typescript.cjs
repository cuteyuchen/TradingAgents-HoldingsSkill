const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')

// 使用仓库已有 TypeScript 编译器，让 Node 原生测试直接执行真实源代码。
require.extensions['.ts'] = (module, filename) => {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  })
  module._compile(output.outputText, filename)
}

const resolveFilename = Module._resolveFilename
Module._resolveFilename = function (request, parent, ...options) {
  const target = request.startsWith('@/') ? path.join(__dirname, '../src', request.slice(2)) : request
  return resolveFilename.call(this, target, parent, ...options)
}

function mockedModule(target, mocks) {
  const loaded = new Module(target, module)
  loaded.filename = target
  loaded.paths = Module._nodeModulePaths(path.dirname(target))
  loaded.require = request => Object.hasOwn(mocks, request) ? mocks[request] : require(Module._resolveFilename(request, loaded))
  return loaded
}

exports.loadWithMocks = (filename, mocks) => {
  const target = path.resolve(__dirname, filename)
  const loaded = mockedModule(target, mocks)
  require.extensions['.ts'](loaded, target)
  return loaded.exports
}

// 通过 Vue 官方编译器执行真实 script setup，生命周期测试不需要挂载 DOM。
exports.loadVueWithMocks = (filename, mocks) => {
  const { parse, compileScript } = require('@vue/compiler-sfc')
  const target = path.resolve(__dirname, filename)
  const { descriptor } = parse(fs.readFileSync(target, 'utf8'), { filename: target })
  const source = compileScript(descriptor, { id: 'offline-review' }).content
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: `${target}.ts`,
  })
  const loaded = mockedModule(target, mocks)
  loaded._compile(output.outputText, target)
  return loaded.exports.default
}
