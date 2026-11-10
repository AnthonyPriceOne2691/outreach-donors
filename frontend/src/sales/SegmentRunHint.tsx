/**
 * Как судья решал прогон — в «i» у заголовка «Прогон №N»: режим судьи, как он понял описание
 * сегмента, второй шанс цитате и сколько сайтов выдачи гипотеза уже собирала.
 *
 * **Замечание владельца 10.11.2026** об экранах продаж: «пояснения — не простынёй, а свёрнуто или в
 * «i»». Аудит «Сбора базы» того же дня насчитал перед таблицей ключей семь абзацев (~165 слов).
 * Эти четыре строки говорят, как устроен прогон, — их читают раз, а не на каждом заходе (правило
 * 10.10.2026: «как устроено» — в «i» у заголовка, на экране — что делать и числа). На экране
 * остаётся то, по чему действуют: шапка прогона строкой, плитки, «На проверку N — решить», лиды,
 * что ждёт повтора с «Повторить сейчас» — и сбой разбора описания (`SegmentScopeLine`): из-за него
 * сайты ждут человека, а то, по чему решают сейчас, не прячется.
 *
 * - **Режим судьи** — так, как он влияет на сайты (`MODE_WORDS`): у прогона в наблюдении «отказ»
 *   кладёт сайт в «Судья отказал бы» — плитка с этим числом на экране.
 * - **Как судья понял описание** (судья сегмента v4, `report.scope`) — страны сегмента и виды сайтов,
 *   с которыми он сверяет каждый сайт. Разбора нет (прогон до v4) — строки нет.
 * - **Второй шанс цитате** (судья v5): у скольких сайтов цитата первого ответа модели не нашлась в
 *   тексте сайта и модель спросили второй раз, и у скольких цитата нового ответа нашлась. Числа
 *   пишет сервер (`report.judge.quote_retried`, `quote_retry_held` — суммой по проходам прогона);
 *   у пересуда свои — в его итоге (`SegmentSitesRejudge`). Число говорит, на чём стоит вердикт, и
 *   читается рядом с режимом и разбором описания, а не в окне «Расход и смета»: токены второго
 *   вопроса сервер отдельно не считает — они в строке судьи окна расхода. Второго вопроса не было
 *   или прогон судили до v5 — строки нет.
 * - **Уже в сборе гипотезы** (`report.serp.known`) — сайты выдачи, которые нашли прошлые прогоны
 *   гипотезы: их не судят и адрес им не ищут.
 */

import { HintLines } from '../components/HintLines';
import { InfoHint } from '../components/InfoHint';
import { capitalized, formatNumber } from '../format';
import type { SegmentRunDetail } from './segmentApi';
import { knownSites, MODE_WORDS, quoteRetries, quoteRetryWords } from './segmentData';
import { scopeFailed, scopeWords } from './SegmentScopeLine';

/** Имя «i» у заголовка прогона: им её находят программа чтения с экрана, тесты и замер контраста
 *  (`scripts/ui_screens_sales_segment.py`). */
export const RUN_HINT = 'Как судья решал прогон';

/** Слова сервера — предложением: с заглавной и с точкой. */
function sentence(words: string): string {
  return `${capitalized(words)}.`;
}

/** Строки «i» по порядку: режим → разбор описания → второй шанс цитате → уже в сборе. Сбой разбора
 *  сюда не идёт — он строкой на экране (`SegmentScopeLine`). */
export function runHintLines(detail: SegmentRunDetail): string[] {
  const { report } = detail;
  const lines = [sentence(MODE_WORDS[detail.run.mode])];
  const scope = scopeFailed(report) ? null : scopeWords(report);
  if (scope !== null) lines.push(`Как судья понял описание: ${scope}.`);
  const retries = quoteRetries(report.judge);
  if (retries !== null) lines.push(sentence(quoteRetryWords(retries)));
  const known = knownSites(detail);
  if (known > 0) {
    lines.push(
      `Уже в сборе гипотезы: ${formatNumber(known)} — эти сайты нашли её прошлые прогоны, их не ` +
        'судим и не ищем снова.',
    );
  }
  return lines;
}

export function SegmentRunHint({ detail }: { detail: SegmentRunDetail }) {
  return (
    <InfoHint name={RUN_HINT} width={360}>
      <HintLines lines={runHintLines(detail)} />
    </InfoHint>
  );
}
