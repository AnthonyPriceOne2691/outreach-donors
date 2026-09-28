/**
 * Имя скачанного файла — из заголовка ответа. Сначала `filename*`: там имя
 * целиком, а «Прайс 2026.pdf» латиницей не пишется, и в простом `filename`
 * сервер оставляет только запасное имя.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { download } from './client';
import { TOKEN_KEY } from '../test/fixtures';
import { serve } from '../test/server';

function fileWith(disposition: string) {
  serve({
    'GET /api/file': {
      raw: 'x',
      headers: { 'content-type': 'application/octet-stream', 'content-disposition': disposition },
    },
  });
  return download('/file');
}

describe('имя скачанного файла', () => {
  beforeEach(() => localStorage.setItem(TOKEN_KEY, 'пропуск'));

  it('берётся из filename* и раскодируется', async () => {
    const got = await fileWith(
      'attachment; filename="2026.pdf"; filename*=UTF-8\'\'%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf',
    );

    expect(got.filename).toBe('Прайс 2026.pdf');
  });

  it('без filename* — простое имя, как у выгрузки доноров', async () => {
    const got = await fileWith('attachment; filename="donors-2026-09-28.csv"');

    expect(got.filename).toBe('donors-2026-09-28.csv');
  });

  it('испорченное filename* уступает запасному имени', async () => {
    const got = await fileWith('attachment; filename="rates.pdf"; filename*=UTF-8\'\'%E0%A4%A.pdf');

    expect(got.filename).toBe('rates.pdf');
  });
});
