#!/usr/bin/env node
// Fails the build if the OpenMRS admin credential — or its variable name —
// ever ends up in the shipped static bundle. Run after `npm run build`
// (docs/UI-001-CLINICAL-UX.md §8's boundary: no privileged credential in
// frontend JavaScript, browser storage, or build artifacts).
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

const uiRoot = resolve(import.meta.dirname, '..')
const repoRoot = resolve(uiRoot, '..')
const distDir = resolve(uiRoot, 'dist')
const envFile = resolve(repoRoot, '.env')

if (!existsSync(distDir)) {
  console.error(`No dist/ directory at ${distDir} — run "npm run build" first.`)
  process.exit(2)
}

function readEnvValue(varName) {
  if (!existsSync(envFile)) return undefined
  const line = readFileSync(envFile, 'utf8')
    .split('\n')
    .find((l) => l.startsWith(`${varName}=`))
  return line ? line.slice(varName.length + 1).trim() : undefined
}

function listFiles(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...listFiles(full))
    } else {
      out.push(full)
    }
  }
  return out
}

const forbiddenNeedles = ['OMRS_ADMIN_PASSWORD', 'KEYCLOAK_OPENMRS_CLIENT_SECRET']

const adminPassword = readEnvValue('OMRS_ADMIN_PASSWORD')
if (adminPassword) {
  forbiddenNeedles.push(adminPassword)
}

const files = listFiles(distDir)
let violations = 0

for (const file of files) {
  const contents = readFileSync(file, 'utf8')
  for (const needle of forbiddenNeedles) {
    if (contents.includes(needle)) {
      console.error(`Forbidden value found in ${file}`)
      violations += 1
    }
  }
}

if (violations > 0) {
  console.error(`${violations} secret-in-bundle violation(s) found. Failing.`)
  process.exit(1)
}

console.log(`No secrets found across ${files.length} built file(s).`)
