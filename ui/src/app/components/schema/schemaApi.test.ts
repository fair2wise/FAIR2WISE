import { afterEach, describe, expect, it, vi } from 'vitest';
import invalid from '../__fixtures__/schema_save_invalid.json';
import reportInvalid from '../__fixtures__/schema_report_invalid.json';
import { ApiError } from '../apiHttp';
import {
  MAX_SCHEMA_BYTES,
  SchemaInvalidError,
  isValidSchemaName,
  parseReport,
  previewRun,
  probeUserSchemas,
  saveSchema,
  schemaErrorText,
  utf8Length,
  validateSchema,
} from './schemaApi';
import { reportOk, stubFetch } from './testRoutes';

afterEach(() => vi.unstubAllGlobals());

describe('name rule', () => {
  it.each(['catfish', 'a', 'fish_kg-2', 'a' + 'b'.repeat(63)])('accepts %s', name => expect(isValidSchemaName(name)).toBe(true));
  it.each(['', 'Catfish', '1fish', '_x', 'a b', 'a/b', '../x', 'a' + 'b'.repeat(64), 'fish.yaml'])('refuses %j', name => expect(isValidSchemaName(name)).toBe(false));
});

describe('parseReport', () => {
  it('reads the pinned server report', () => {
    const report = parseReport(reportInvalid);
    expect(report?.ok).toBe(false);
    expect(report?.problems.map(p => p.line)).toEqual([2, 43]);
    expect(report?.problems[1].column).toBeNull();
    expect(report?.checked).toHaveLength(5);
    expect(report?.note).toContain('Not full LinkML');
  });
  it('refuses a body that is not a report', () => {
    expect(parseReport(null)).toBeNull();
    expect(parseReport({ ok: 'yes' })).toBeNull();
  });
});

describe('requests', () => {
  it('validate sends the text and passes the abort signal', async () => {
    const calls = stubFetch({ 'POST /v1/library/schemas/validate': { status: 200, body: reportOk } });
    const controller = new AbortController();
    const report = await validateSchema('x: 1', controller.signal);
    expect(report.ok).toBe(true);
    expect(calls[0].body).toEqual({ text: 'x: 1' });
    expect(calls[0].signal).toBe(controller.signal);
  });

  it('a 422 on save throws the report with its lines', async () => {
    stubFetch({ 'POST /v1/library/schemas': { status: 422, body: invalid } });
    const error = await saveSchema('catfish', 'bad').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SchemaInvalidError);
    expect((error as SchemaInvalidError).report.problems[0].code).toBe('name_mismatch');
    expect((error as SchemaInvalidError).reason).toBe('schema_invalid');
  });

  it('a 422 without a report is a plain ApiError', async () => {
    stubFetch({ 'POST /v1/library/schemas': { status: 422, body: { code: 'invalid_input', message: 'bad name', retryable: false } } });
    const error = await saveSchema('A', 'x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(SchemaInvalidError);
    expect((error as ApiError).message).toBe('bad name');
  });

  it('the confirmed preview sends confirm and the token', async () => {
    const calls = stubFetch({ 'POST /v1/library/schemas/preview': { status: 200, body: {} } });
    await previewRun({ schema: 's', paper_id: 'p1', confirmation: 'tok' });
    expect(calls[0].body).toEqual({ schema: 's', paper_id: 'p1', confirmation: 'tok', confirm: true });
  });

  it.each([404, 403, 401])('the probe resolves null on %i', async status => {
    stubFetch({ 'GET /v1/library/schemas/user': { status, body: {} } });
    expect(await probeUserSchemas()).toBeNull();
  });

  it('the probe resolves null when the API is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await probeUserSchemas()).toBeNull();
  });
});

describe('schemaErrorText', () => {
  const err = (status: number, reason: string | null, code: string | null = null, message = 'server words') => new ApiError(message, status, code, reason);
  it('says each typed generate failure in plain words', () => {
    expect(schemaErrorText(err(501, null, 'unsupported_capability', 'no LLM backend is configured for schema generation'), 'generate')).toContain('no LLM backend is configured');
    expect(schemaErrorText(err(403, null, 'denied'), 'generate')).toContain('not on the free list');
    expect(schemaErrorText(err(503, 'model_timeout'), 'generate')).toContain('did not answer in time');
    expect(schemaErrorText(err(422, 'body_too_large'), 'generate')).toContain('256 KiB');
    expect(schemaErrorText(err(503, 'response_too_large'), 'generate')).toContain('too much text');
    expect(schemaErrorText(err(503, 'ollama_unreachable'), 'generate')).toContain('Ollama');
    expect(schemaErrorText(err(503, 'cborg_ip_not_authorized'), 'generate')).toContain('network address');
    expect(schemaErrorText(err(403, null, 'denied'), 'save')).toContain('not authorized');
    expect(schemaErrorText(err(401, null), 'save')).toContain('not authorized');
  });
  it('explains a schema in use and a stale estimate', () => {
    expect(schemaErrorText(err(409, 'schema_in_use'))).toContain('KG was built on this version');
    expect(schemaErrorText(err(409, 'stale_approval'), 'preview')).toContain('Estimate again');
  });
  it('falls back to the server message', () => {
    expect(schemaErrorText(err(500, null))).toBe('server words');
  });
});

describe('size', () => {
  it('counts UTF-8 bytes, not characters', () => {
    expect(utf8Length('aé')).toBe(3);
    expect(MAX_SCHEMA_BYTES).toBe(262144);
  });
});
