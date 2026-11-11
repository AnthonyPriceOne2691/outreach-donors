/**
 * «Судья сам, каждую ночь»: строки над очередью спорных и переключатель полосы в окне применения.
 *
 * Слово владельца 10–11.11.2026: кнопка «Применить советы…» — ручной запуск, а разбирать спорных
 * руками некому — то же идёт само каждую ночь после обхода доноров (`crawl/judge_night.py`), но
 * только по полосам, которые человек включил.
 *
 * **Над очередью — две короткие строки** (`docs/UI_RULES.md`, 10.11.2026: итог одной строкой,
 * остальное по запросу). Первая — как стоят полосы: «Судья сам, каждую ночь: не пишем — вкл ·
 * пишем — выкл». Вторая — что судья сделал последней ночью: «Ночью 05.11: разобрано 34 · не пишем
 * 20 · пишем 9 · не понять 5»; строки итога разбора, токены и почему встал — в «i», как их отдал
 * сервер. Полосы, выключенной той ночью, в строке нет: «пишем 0» читалось бы как «судья ничего
 * не посоветовал». Шаг идёт — строка говорит, что он делает, и обновляется сама.
 *
 * **Что остаётся человеку после «пишем» — рядом числом**: «К письму ждут перевода: N» ведёт к блоку
 * «К письму». Ночь никого не переводит: «Перевести» ставит поиск адресов, а это отдельное решение.
 *
 * **Включение — в окне, после плана полосы, и с подтверждением у кнопки** (`ConfirmPopover`): с этой
 * минуты решения ставятся без человека, и вопрос называет потолок ночи, что останется человеку и
 * когда шаг пойдёт в первый раз. **Выключение — одним нажатием**: остановить автомат не должно быть
 * труднее, чем запустить.
 */

import { Alert, Anchor, Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { JudgeBand } from '../api/advertisersJudgeApply';
import { fetchJudgeNight, JUDGE_NIGHT_KEY, setJudgeBand } from '../api/advertisersJudgeNight';
import type { JudgeBandState, JudgeNight, JudgeNightSummary } from '../api/advertisersJudgeNight';
import { refusalOf } from '../api/client';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { InfoHint } from '../components/InfoHint';
import { formatDate, formatNumber } from '../format';
import { BAND_WORDS, counted } from './judgeApplyWords';

/** Пока шаг идёт, строка перечитывается сама. */
const POLL_MS = 30_000;
/** Блок «К письму» под очередью: к нему ведёт «ждут перевода». */
export const TO_LETTER_ID = 'advertisers-to-letter';
const BANDS: readonly JudgeBand[] = ['skip', 'write'];

export function useJudgeNight() {
  return useQuery({
    queryKey: JUDGE_NIGHT_KEY,
    queryFn: fetchJudgeNight,
    refetchInterval: (query) => (query.state.data?.last?.going ? POLL_MS : false),
  });
}

/** «не пишем — вкл · пишем — выкл». */
export function bandsWords(bands: JudgeBandState[]): string {
  return bands
    .map((state) => `${BAND_WORDS[state.band]} — ${state.enabled ? 'вкл' : 'выкл'}`)
    .join(' · ');
}

/** «2026-11-05» → «05.11»: год в строке итога ночи лишний. */
function dayMonth(night: string): string {
  const [, month, day] = night.split('-');
  return `${day}.${month}`;
}

/** «Ночью 05.11: разобрано 34 · не пишем 20 · пишем 9 · не понять 5». Шаг идёт — что он делает;
 *  не дошёл до конца — так и сказано, причина — в «i». */
export function nightWords(last: JudgeNightSummary): string {
  const head = `Ночью ${dayMonth(last.night)}`;
  if (last.going !== null) return `${head}: ${last.going}`;
  if (last.status === 'failed') return `${head}: шаг не дошёл до конца`;
  const parts = [counted('разобрано', last.judged)];
  for (const band of BANDS) {
    const applied = last.applied[band];
    if (applied !== undefined) parts.push(counted(BAND_WORDS[band], applied));
  }
  parts.push(counted('не понять', last.unclear));
  if (last.why !== null) parts.push('разбор встал раньше конца');
  return `${head}: ${parts.join(' · ')}`;
}

/** Итог последней ночи строкой; подробности — в «i» строками сервера. */
function LastNight({ last }: { last: JudgeNightSummary }) {
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <Text size="sm" c="dimmed">
        {nightWords(last)}
      </Text>
      {last.lines.length > 0 ? (
        <InfoHint name="Итог ночи целиком" width={340} interactive>
          <Stack gap={4}>
            {last.lines.map((line) => (
              <Text key={line} size="sm">
                {line}
              </Text>
            ))}
          </Stack>
        </InfoHint>
      ) : null}
    </Group>
  );
}

/** «К письму ждут перевода: N» — ведёт к блоку «К письму»: ночь никого не переводит. */
function AwaitingPromotion({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <Anchor
      component="button"
      type="button"
      size="sm"
      onClick={() => document.getElementById(TO_LETTER_ID)?.scrollIntoView({ behavior: 'smooth' })}
    >
      К письму ждут перевода: {formatNumber(count)}
    </Anchor>
  );
}

/** Строки над очередью: как стоят полосы, что было последней ночью и что ждёт человека. */
export function JudgeNightLines() {
  const { data } = useJudgeNight();
  if (data === undefined) return null;
  return (
    <Stack gap={4} align="flex-start">
      <Text size="sm" c="dimmed">
        Судья сам, каждую ночь: {bandsWords(data.bands)}
      </Text>
      {data.last !== null ? <LastNight last={data.last} /> : null}
      <AwaitingPromotion count={data.awaiting_promotion} />
    </Stack>
  );
}

/** Что включение значит — до нажатия: потолок ночи, что останется человеку и когда первый шаг. */
export function enableWarning(band: JudgeBand, night: JudgeNight): string {
  const what = `Судья будет ставить «${BAND_WORDS[band]}» сам каждую ночь после обхода доноров — не больше ${formatNumber(night.cap)} пар за ночь.`;
  const kept =
    band === 'skip'
      ? 'Пары, чьё «не пишем» сняло бы рекламодателя, останутся человеку.'
      : 'Только парам с известным DR; рекламодателями домены станут после «Перевести» — ночь этого не делает.';
  const when = night.ran_today
    ? 'Первый раз — ближайшей ночью.'
    : 'Сегодняшний шаг ещё не шёл: он начнётся в ближайшие минуты.';
  return `${what} ${kept} ${when}`;
}

/** Кто и когда включил полосу — если это известно. */
function setWords(state: JudgeBandState): string {
  if (state.set_at === null) return '';
  const who = state.set_by === null ? '' : ` ${state.set_by}`;
  return ` — включил${who} ${formatDate(state.set_at)}`;
}

/** Автоприменение выбранной полосы в окне применения: включить после плана, выключить сразу. */
export function JudgeNightSwitch({
  band,
  ready,
}: {
  band: JudgeBand;
  /** План полосы показан и порции не идут: включать можно. */
  ready: boolean;
}) {
  const client = useQueryClient();
  const { data: night } = useJudgeNight();
  const change = useMutation({
    mutationFn: (enabled: boolean) => setJudgeBand(band, enabled),
    onSuccess: (view) => client.setQueryData(JUDGE_NIGHT_KEY, view),
  });
  const state = night?.bands.find((one) => one.band === band);
  if (night === undefined || state === undefined) return null;
  return (
    <Stack gap={6} role="group" aria-label="Автоприменение полосы каждую ночь">
      <Text size="sm" fw={500}>
        Каждую ночь
      </Text>
      {change.error !== null && (
        <Alert color="red" title="Полоса не переключена">
          {refusalOf(change.error)}
        </Alert>
      )}
      {state.enabled ? (
        <Group gap="sm" justify="space-between" wrap="nowrap" align="flex-start">
          <Text size="sm">
            Судья ставит «{BAND_WORDS[band]}» сам, после обхода доноров{setWords(state)}.
          </Text>
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            loading={change.isPending}
            onClick={() => change.mutate(false)}
          >
            Выключить
          </Button>
        </Group>
      ) : (
        <Group gap="sm" justify="space-between" wrap="nowrap" align="flex-start">
          <Text size="sm" c="dimmed">
            Сейчас «{BAND_WORDS[band]}» ставит только кнопка «Применить».
          </Text>
          <ConfirmPopover
            message={enableWarning(band, night)}
            confirm="Включить"
            onConfirm={() => change.mutate(true)}
          >
            {(ask) => (
              <Button
                size="compact-sm"
                variant="default"
                className="press"
                loading={change.isPending}
                disabled={!ready}
                onClick={ask}
              >
                Применять каждую ночь…
              </Button>
            )}
          </ConfirmPopover>
        </Group>
      )}
      {band === 'write' && night.awaiting_promotion > 0 ? (
        <Text size="sm">
          К письму ждут перевода: {formatNumber(night.awaiting_promotion)} — «Перевести» в блоке «К
          письму».
        </Text>
      ) : null}
    </Stack>
  );
}
