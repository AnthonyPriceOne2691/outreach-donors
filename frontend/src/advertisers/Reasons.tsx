/**
 * Причины балла скоринга — строками, по строке на причину, одним видом в очереди проверки
 * (`CandidatesTable`) и во «Всех рекламодателях» (`AdvertiserListTable`): человек сверяет
 * рекламодателя по ним, а не по числу.
 *
 * **Балл не отрывается от причины** (разведка экранов 10.11.2026): в узкой колонке «+2» снова
 * вставало своей строкой под «коммерческая ссылка под nofollow». Перед «+N» — неразрывный пробел:
 * на новую строку уходит слово перед баллом вместе с ним. В файле и в адресе причина — как написал
 * сервер.
 *
 * **Пояснение причины — в «i» у неё** (`reason_notes` сервера, `crawl/reason_words.py`): в строке
 * «зона .org», почему её решает человек — по наведению и касанию. Одиннадцать слов у каждого домена
 * .org растили строку таблицы на три-четыре строки. Сервер старше экрана пояснений не шлёт —
 * причина стоит, как пришла.
 */

import { Box, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoHint } from '../components/InfoHint';

/** «… nofollow +2» — с неразрывным пробелом перед баллом: «+2» не встаёт строкой одно. */
export function keptPoints(reason: string): string {
  return reason.replace(/ (\+\d+)$/, '\u00a0$1');
}

function Reason({ reason, note }: { reason: string; note: string | undefined }) {
  if (note === undefined) return <Text size="xs">{keptPoints(reason)}</Text>;
  return (
    <Text size="xs">
      {/* Слова и «i» — одним куском: значок не уезжает на строку без своей причины. */}
      <Box component="span" display="inline-flex" style={{ alignItems: 'center', gap: 2 }}>
        {keptPoints(reason)}
        <InfoHint name={`Что значит «${reason}»`} width={240}>
          {note}
        </InfoHint>
      </Box>
    </Text>
  );
}

export function Reasons({
  reasons,
  notes,
  children,
}: {
  reasons: readonly string[];
  /** Пояснение причины по её словам; нет поля — сервер старше экрана. */
  notes?: Readonly<Record<string, string>> | undefined;
  /** Строка под причинами: сколько ссылок и страниц. */
  children?: ReactNode;
}) {
  return (
    <Stack gap={2}>
      {reasons.map((reason) => (
        <Reason key={reason} reason={reason} note={notes?.[reason]} />
      ))}
      {children}
    </Stack>
  );
}
