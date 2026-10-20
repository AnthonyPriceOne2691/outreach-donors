/**
 * Тематика на экране прогона: во что обойдётся — в смете, и чего не вышло — в истории.
 *
 * **Смета называет токены тематики отдельной строкой** (`TopicTokens`): это дневной потолок
 * модели, а не юниты Ahrefs, и в бюджет прогона они не входят. Сайтам, у которых тема уже
 * есть, модель второй раз не зовут — число в смете — худший случай, как у юнитов.
 *
 * **«Темы нет» — меткой у прогона** (`TopicSilence`), как у судьи: модель не ответила или
 * ответила не по форме (проза, обрезанный ответ, тема не из списка). Ноль определённых при
 * неработающем ключе выглядел бы как «тем нет». Первая причина — в подсказке.
 */

import { Badge, Text } from '@mantine/core';

import type { Forecast } from '../api/types';
import { formatCompact, formatNumber } from '../format';

/** Поля сметы о тематике — у прогона по ключам и по ссылкам конкурентов они одни. */
type TopicFigures = Pick<Forecast, 'topic_tokens' | 'topic_tokens_per_site' | 'topic_measured'>;

export function TopicTokens({ forecast }: { forecast: TopicFigures }) {
  const tokens = forecast.topic_tokens ?? 0;
  if (tokens <= 0) return null;
  const how = forecast.topic_measured
    ? 'по замеру прошлых прогонов'
    : 'по оценке — замера ещё не было';
  return (
    <Text size="sm" c="dimmed" className="topicTokens">
      Тематика сайтов — ещё до <b>{formatCompact(tokens)}</b> токенов модели:{' '}
      {formatNumber(forecast.topic_tokens_per_site ?? 0)} на сайт, {how}. Сайтам с темой не
      тратится; счёт — в дневной потолок модели, а не в юниты Ahrefs.
    </Text>
  );
}

/** Число из ветки тем отчёта: прогоны до 20.10.2026 «не по форме» не считали — ноль. */
function countOf(said: Record<string, unknown>, key: string): number {
  const value = said[key];
  return typeof value === 'number' && value > 0 ? value : 0;
}

/** Первая названная причина: сначала «не ответила», потом «не по форме». */
function reasonOf(said: Record<string, unknown>): string | undefined {
  const why = [said['unanswered_reason'], said['unreadable_reason']].find(
    (reason) => typeof reason === 'string' && reason !== '',
  );
  return typeof why === 'string' ? why : undefined;
}

export function TopicSilence({ run }: { run: { stats: Record<string, unknown> | null } }) {
  const topics = run.stats?.['topics'];
  if (typeof topics !== 'object' || topics === null) return null;
  const said = topics as Record<string, unknown>;
  const silent = countOf(said, 'unanswered') + countOf(said, 'unreadable');
  if (silent <= 0) return null;
  // Своей строкой: в ряд со значком «расходится …» метка выходила за колонку судьи.
  return (
    <div className="topicSilence">
      <Badge variant="light" size="sm" color="red" title={reasonOf(said)}>
        тематика: нет ответа {silent}
      </Badge>
    </div>
  );
}
