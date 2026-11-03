/**
 * Доля ложных рекламодателей и ошибка базы «писать всем спорным» — строками фактов в шапке
 * очереди проверки.
 *
 * Сверка ТЗ 11.10.2026: требование — «не более 10% ложных рекламодателей», а экран
 * существовал ради этого числа и его не показывал (`okf/link-scoring.md`). Ложный —
 * «куплено» скоринга, которому человек сказал «не пишем».
 *
 * **Доля — по случайной выборке** (решение Anthony 12.10.2026, `crawl/sample.py`):
 * очередь идёт от сильных к слабым, и по всем решённым доля занижена. Выборка —
 * около пятой части доменов, постоянная; очередь ставит их среди остальных, и человек их не
 * отличает — экран не говорит, какие строки в выборке.
 *
 * **Спорные — тем же правилом** (замер спорных, 02.11.2026): их доля «не пишем» — то, сколько
 * ложных дала бы база «писать всем спорным». Её обязан побить любой автоматический совет.
 *
 * **Вывод — по границам доли, от сервера** (`crawl/accuracy.py`, 03.11.2026): «с запасом на
 * случай» — односторонняя граница 95 %. Верхняя не выше допуска — в допуске, нижняя выше —
 * вне допуска, иначе вывода нет, и сказано, сколько решений без «не пишем» ещё нужно. До этого
 * вывода не было до 200 решений, и у «куплено» он не наступил бы никогда.
 *
 * **Строкой, а не плитками** (как замеры судьи на «Отборе», 10.10.2026): действуют здесь
 * по одному числу — значок с долей: мята — в допуске, роза — за ним, серый — вывода нет.
 */

import { Badge, Group, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { SampleShare, ScoringCheck, Within } from '../api/advertisers';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, formatPercent, plural } from '../format';

const ABOUT =
  'Ложный рекламодатель — «куплено» скоринга, которому человек сказал «Не пишем»: перевод ' +
  'берёт «куплено» к письму сам, и ложный среди них — оффер тому, кто ничего не покупал. ' +
  'Доля — по случайной выборке: около пятой части доменов, постоянной. Очередь ставит их ' +
  'среди остальных, и решать их — как любую строку. «С запасом на случай» — граница, за ' +
  'которую доля выходит не чаще раза из двадцати: на горстке решений она широка, и вывода ' +
  'нет, пока граница не легла по одну сторону допуска. «Не пишем» среди спорных — сколько ' +
  'ложных дало бы правило «писать всем спорным». Балл решением не переписывается.';

const COLORS: Record<Within, string> = {
  within: 'green',
  outside: 'red',
  unproven: 'gray',
  unmeasured: 'gray',
};

function decisionsWord(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'решение', 'решения', 'решений')}`;
}

/** Сколько ещё решить — и честно, когда нынешней выборкой этого не набрать. */
function toGoWords(sample: SampleShare): string {
  if (sample.to_go === null) return '';
  const need = `для «в допуске» нужно ещё ${decisionsWord(sample.to_go)} без «не пишем»`;
  if (sample.to_go <= sample.left) return need;
  return `${need}, а нерешённых в выборке ${formatNumber(sample.left)}: вывод придёт с новыми обходами`;
}

/** Слова строки одной выборки: чья она и что значит каждый вывод. */
interface LineWords {
  /** Выборка пуста: «в случайной выборке «куплено» пока нет». */
  empty: string;
  /** Строки есть, решений нет: «по выборке решений пока нет». */
  undecided: string;
  /** Начало строки: «по случайной выборке». */
  head: string;
  outcomes: Record<Exclude<Within, 'unmeasured'>, string>;
}

function decided(sample: SampleShare): string {
  return `${decisionsWord(sample.decided)}, «не пишем» ${formatNumber(sample.skip)}`;
}

/** Вывод словами: граница, которая его даёт, и что из неё следует. */
function boundWords(sample: SampleShare, outcomes: LineWords['outcomes']): string {
  if (sample.low === null || sample.high === null || sample.within === 'unmeasured') return '';
  const bound =
    sample.within === 'within'
      ? `не больше ${formatPercent(sample.high)}`
      : sample.within === 'outside'
        ? `не меньше ${formatPercent(sample.low)}`
        : `от ${formatPercent(sample.low)} до ${formatPercent(sample.high)}`;
  return `с запасом на случай — ${bound}: ${outcomes[sample.within]}`;
}

/** Строка выборки: решения, граница, вывод и — пока вывода нет — сколько ещё решить. */
function sampleLine(sample: SampleShare, words: LineWords): string {
  if (sample.within === 'unmeasured') {
    return sample.total === 0
      ? `мерить нечем: ${words.empty}`
      : `мерить нечем: ${words.undecided}; ${toGoWords(sample)}`;
  }
  const bound = boundWords(sample, words.outcomes);
  const toGo = sample.within === 'unproven' ? `; ${toGoWords(sample)}` : '';
  return `${words.head}: ${decided(sample)}; ${bound}${toGo}`;
}

const BOUGHT_WORDS: LineWords = {
  empty: 'в случайной выборке «куплено» пока нет',
  undecided: 'по выборке решений пока нет',
  head: 'по случайной выборке',
  outcomes: { within: 'в допуске', outside: 'вне допуска', unproven: 'вывода нет' },
};

const DISPUTED_WORDS: LineWords = {
  empty: 'в случайной выборке спорных пока нет',
  undecided: 'по выборке спорных решений пока нет',
  head: 'по случайной выборке спорных',
  outcomes: {
    within: 'писать всем спорным можно было бы в допуске',
    outside: 'писать всем спорным нельзя — ложных за допуском',
    unproven: 'можно ли писать всем спорным, не доказано',
  },
};

/** Что за долей «куплено» — словами: решения выборки, граница, вывод, допуск ТЗ. */
export function falseShareLine(check: ScoringCheck): string {
  const limit = `допуск ТЗ — не больше ${formatPercent(check.false_limit)}`;
  return `${sampleLine(check.bought_sample, BOUGHT_WORDS)}; ${limit}`;
}

/** Что за долей «не пишем» среди спорных — словами: сколько ложных дало бы «писать всем». */
export function disputedLine(check: ScoringCheck): string {
  return sampleLine(check.pending_sample, DISPUTED_WORDS);
}

function ShareLine({
  title,
  sample,
  words,
  children,
}: {
  title: string;
  sample: SampleShare;
  words: string;
  children?: ReactNode;
}) {
  return (
    <Group gap="xs" align="center">
      <Text size="sm">{title}</Text>
      <Badge variant="light" color={COLORS[sample.within]}>
        {formatPercent(sample.share)}
      </Badge>
      <Text size="sm" c="dimmed">
        {words}
      </Text>
      {children}
    </Group>
  );
}

export function FalseShare({ check }: { check: ScoringCheck }) {
  return (
    <Stack gap={6}>
      <ShareLine
        title="Ложных в «куплено»:"
        sample={check.bought_sample}
        words={falseShareLine(check)}
      >
        <InfoHint name="Как считается доля ложных" width={360}>
          {ABOUT}
        </InfoHint>
      </ShareLine>
      <ShareLine
        title="«Не пишем» среди спорных:"
        sample={check.pending_sample}
        words={disputedLine(check)}
      />
    </Stack>
  );
}
