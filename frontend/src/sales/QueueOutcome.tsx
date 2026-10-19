/**
 * Итог сборки очереди, после которой отправлять нечего, — плашкой, а не мелкой строкой.
 *
 * **Почему плашка** (просьба владельца 16.10.2026: «нужно как-то выделить, что отправки не будет,
 * потому что написано мелко, … указать пользователю, что не так»). Сборка кончилась, новых писем
 * нет, а под ней — «Сборка очереди продаж: готово» зелёным и строка «Новых писем 0 … Ждут —
 * цепочка … задана не целиком: 47» мелким шрифтом: что письма не уйдут, приходилось вычитывать.
 *
 * **Когда**: сборка кончилась, новых и собранных заново писем нет, и очередь продаж пуста —
 * пачке отправлять нечего. Собраны письма или в очереди есть другие — плашки нет: отправка будет,
 * а кто ждёт и почему, говорит строка задачи. **Что в ней**: почему лиды ждут — словами сервера
 * с числом; если у какого-то языка цепочка не полна — каких шагов нет (то же, что строка цепочки
 * над плитками) и вход в «Цепочку писем»; остановлена сборка — почему.
 */

import { Alert, Button, Group, List, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { fetchJob } from '../api/jobs';
import { chainLanguageTitle } from '../api/salesLabels';
import type { ChainState, SalesQueueView } from '../api/salesTypes';
import { formatNumber, plural } from '../format';
import { salesTabLink } from './leadFilters';
import { builtLetters, waitingOf } from './queueData';

function leads(count: number): string {
  return plural(count, 'лид', 'лида', 'лидов');
}

/** Отправлять нечего: сборка не написала ни письма, и очередь продаж пуста. */
export function nothingToSend(report: Record<string, unknown>, view: SalesQueueView): boolean {
  return builtLetters(report) === 0 && view.stage_queued === 0;
}

function Waiting({ report }: { report: Record<string, unknown> }) {
  const waiting = waitingOf(report);
  if (waiting.length === 0) return null;
  return (
    <>
      <Text size="sm">Ни одному лиду письмо не собрано. Почему ждут:</Text>
      <List size="sm" spacing={2}>
        {waiting.map(([why, count]) => (
          <List.Item key={why}>
            {why} — {formatNumber(count)} {leads(count)}
          </List.Item>
        ))}
      </List>
    </>
  );
}

/** Неполные цепочки тех языков, чьи лиды ждут, — с числом ждущих: цепочка языка без лидов — не
 *  обязанность, и названная рядом она читалась бы так (замечание к снимку 16.10.2026). */
function gapsOf(view: SalesQueueView): [ChainState, number][] {
  return view.chains
    .filter((state) => state.missing.length > 0)
    .map((state): [ChainState, number] => [state, view.unwritten_languages[state.language] ?? 0])
    .filter(([, count]) => count > 0);
}

function ChainGaps({ view }: { view: SalesQueueView }) {
  const gaps = gapsOf(view);
  if (gaps.length === 0) return null;
  return (
    <>
      <Text size="sm">Заполните «Цепочку писем» и соберите очередь снова:</Text>
      <List size="sm" spacing={2}>
        {gaps.map(([state, count]) => (
          <List.Item key={state.language}>
            {chainLanguageTitle(state.language)} — нет {state.missing.join(', ')}; ждут{' '}
            {formatNumber(count)} {leads(count)}
          </List.Item>
        ))}
      </List>
      <Group>
        <Button
          component={Link}
          to={salesTabLink('chain', view.hypothesis_id)}
          variant="default"
          size="compact-sm"
          className="press"
        >
          Открыть «Цепочку писем»
        </Button>
      </Group>
    </>
  );
}

/** Плашка «отправлять нечего» под строкой задачи сборки. Исход задачи — из того же кэша, что у
 *  `JobLine`: второго опроса нет. */
export function QueueOutcome({ jobId, view }: { jobId: string; view: SalesQueueView }) {
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId),
    retry: false,
  });
  const report = job.data?.state === 'done' ? job.data.report : null;
  if (report === null || !nothingToSend(report, view)) return null;
  return (
    <Alert color="yellow" title="Писем не собрано — отправлять нечего" className="queueNothing">
      <Stack gap={6}>
        <Waiting report={report} />
        {typeof report.stopped === 'string' && (
          <Text size="sm">Сборка остановлена: {report.stopped}</Text>
        )}
        <ChainGaps view={view} />
      </Stack>
    </Alert>
  );
}
