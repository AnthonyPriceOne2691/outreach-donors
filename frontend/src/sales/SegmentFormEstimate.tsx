/**
 * Смета формы «Новый прогон» (`SegmentForm`): последняя посчитанная, свежая ли она, пересчёт
 * после галочки и блок сметы — ключи чипами, строки статей, отказ.
 *
 * **Пересчёт — сам, через полсекунды после последнего щелчка** (`RECOUNT_MS`): три щелчка подряд —
 * один запрос. Пока смета не пересчитана, она видна приглушённой, а запуск закрыт: свежая — та,
 * что посчитана ровно по нынешним значениям формы, снятым ключам и описанию на экране.
 *
 * **Ответ ложится, только если он про то, что на экране сейчас**: щёлкнули дальше — ответ на
 * прошлое состояние галочек новое не перебивает. Смета, посчитанная по другому описанию
 * сегмента, чем на экране (его поправили в соседней вкладке), не показывается: описание
 * перечитывается, смету считают заново.
 */

import { Alert, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { estimateRun } from './segmentApi';
import type { SegmentAsk, SegmentEstimate } from './segmentApi';
import { SEGMENT_QUERY_KEY } from './segmentData';
import { SegmentCheck } from './SegmentHelper';
import { EXCLUDED_COUNT, estimateChips } from './segmentKeys';
import { KeywordChips } from './SegmentKeywords';

/** Пересчёт сметы после галочки — через столько после последнего щелчка: три щелчка подряд —
 *  один запрос. */
export const RECOUNT_MS = 500;

/** Что считали: значения формы с снятыми ключами и описание сегмента на экране. `base` — то же
 *  без снятых: смета той же основы видна и во время пересчёта после галочки. */
export interface Asked {
  ask: SegmentAsk;
  segment: string | null;
  key: string;
  base: string;
}

interface Counted {
  key: string;
  base: string;
  found: SegmentEstimate;
}

/** Описание, по которому посчитана смета, — то же, что на экране. */
function sameSegment(counted: string, shown: string | null): boolean {
  return counted.trim() === (shown ?? '').trim();
}

/** Смета словами сервера: ключи чипами, строки статей и отказ. Пока смета пересчитывается после
 *  галочки, её строки приглушены, отказа нет, а у чипов — снятое на экране. Чипы — первыми: всё,
 *  что меняется при пересчёте, стоит ниже них и не двигает их под курсором. Последней — проверка
 *  описания помощником (`SegmentCheck`): по описанию, по которому посчитана смета. */
export function EstimateBlock({
  found,
  fresh,
  failed,
  excluded,
  onToggle,
  onPropose,
}: {
  found: SegmentEstimate;
  fresh: boolean;
  /** Пересчёт не прошёл: смета на экране — прошлая, считают её кнопкой. */
  failed: boolean;
  excluded: number[];
  onToggle: (line: number, taken: boolean) => void;
  /** «Взять в описание» у проверки описания: предложение помощника — в правку описания. */
  onPropose: (text: string) => void;
}) {
  const note = fresh
    ? 'Снимите галочку — ключ не покупается, смета пересчитается сама.'
    : failed
      ? 'Смета не пересчитана — посчитайте её кнопкой «Посчитать смету».'
      : 'Пересчитываем смету…';
  return (
    <Stack gap={6} aria-label="Смета прогона" aria-busy={!fresh && !failed}>
      <KeywordChips
        label="Ключи сметы"
        chips={estimateChips(found, fresh ? null : new Set(excluded))}
        excludedWords={EXCLUDED_COUNT}
        onToggle={onToggle}
        note={note}
      />
      {found.lines.length > 0 && (
        <Stack gap={2} className="segmentEstimate">
          {found.lines.map((line) => (
            <Text key={line} size="sm" {...(fresh ? {} : { c: 'dimmed' })}>
              {line}
            </Text>
          ))}
        </Stack>
      )}
      {fresh && found.refusal !== null && (
        <Alert color="red" title="Прогон не запустится">
          {found.refusal}
        </Alert>
      )}
      <SegmentCheck segment={found.segment} onTake={onPropose} />
    </Stack>
  );
}

/** Смета формы: последняя посчитанная, свежая ли она, и пересчёт после галочки. */
export function useEstimate(now: Asked) {
  const client = useQueryClient();
  const [counted, setCounted] = useState<Counted | null>(null);
  const [moved, setMoved] = useState(false);
  // Ответ сметы ложится, только если он про то, что на экране сейчас: щёлкнули дальше — ответ
  // на прошлое состояние галочек не перебивает новое.
  const current = useRef(now.key);
  useLayoutEffect(() => {
    current.current = now.key;
  }, [now.key]);
  const count = useMutation({
    mutationFn: (sent: Asked) => estimateRun(sent.ask),
    onMutate: () => setMoved(false),
    onSuccess: (found, sent) => {
      if (sent.key !== current.current) return;
      if (!sameSegment(found.segment, sent.segment)) {
        // Описание поправили не здесь: смета посчитана не по тому, что на экране.
        setMoved(true);
        void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
        return;
      }
      setCounted({ key: sent.key, base: sent.base, found });
    },
  });
  const shown = counted !== null && counted.base === now.base ? counted : null;
  const fresh = shown !== null && shown.key === now.key;
  const recount = shown !== null && !fresh;
  const { mutate } = count;
  const { key, base } = now;
  useEffect(() => {
    if (!recount) return undefined;
    const timer = window.setTimeout(() => {
      const sent = JSON.parse(key) as Pick<Asked, 'ask' | 'segment'>;
      mutate({ ...sent, key, base });
    }, RECOUNT_MS);
    return () => window.clearTimeout(timer);
  }, [recount, key, base, mutate]);
  return { count, shown, fresh, moved, drop: () => setCounted(null) };
}
