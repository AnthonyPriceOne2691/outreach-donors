/**
 * «Советы судьи» над очередью спорных: итог одной строкой и кнопка «Применить советы…».
 *
 * Решение владельца 10.11.2026: спорных в очереди сотни, совет судьи есть почти у каждой, а людей,
 * которые разберут их руками, нет. Советы применяются решениями — полосами «не пишем» и «пишем», в
 * окне с планом (`JudgeApplyModal`); «не понять» остаётся человеку.
 *
 * **Строка — числа, а не советы строкам.** Совет рядом с нерешённой парой тянул бы решение за собой
 * (`crawl/judge_advice.py`), поэтому на виду только сколько пар с каким советом: «не пишем 120 ·
 * пишем 80 · не понять 30 · без совета 20». Числа сходятся в «Ждут человека»: «ждут DR» и «совет
 * устарел» названы, только когда они не ноль.
 *
 * **Кнопка и строка — порознь**, как у «Переобойти спорные»: кнопка — в ряду с выгрузкой, строка —
 * под ним. Запрос один (`JUDGE_APPLY_KEY`) и лежит под ключом очереди: решение человека и конец
 * разбора перечитывают его вместе с ней.
 *
 * Применять может учётка с правом запуска и правом решения разом — без них кнопки нет, а кто
 * применяет, сказано в «i» у строки.
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchJudgeStanding, JUDGE_APPLY_KEY } from '../api/advertisersJudgeApply';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { InfoHint } from '../components/InfoHint';
import { JudgeApplyModal } from './JudgeApplyModal';
import { standingWords } from './judgeApplyWords';

export function useJudgeStanding() {
  return useQuery({ queryKey: JUDGE_APPLY_KEY, queryFn: fetchJudgeStanding });
}

/** Может ли учётка применять и откатывать: право запуска и право решения разом. */
function useMayApply(): boolean {
  const { can } = useSession();
  return can('run') && can('prices');
}

/** Кнопка с окном плана — с обоими правами; без них кнопки нет. */
export function JudgeApplyButton() {
  const mayApply = useMayApply();
  const [opened, setOpened] = useState(false);
  const { data: standing } = useJudgeStanding();
  if (!mayApply) return null;
  return (
    <>
      <Button
        variant="default"
        size="xs"
        className="press"
        aria-haspopup="dialog"
        onClick={() => setOpened(true)}
      >
        Применить советы…
      </Button>
      {opened ? <JudgeApplyModal standing={standing} onClose={() => setOpened(false)} /> : null}
    </>
  );
}

/** Что за числа и кто применяет — окошком: длиннее строки подсказки. */
function About({ mayApply }: { mayApply: boolean }) {
  return (
    <InfoHint name="Что значат советы судьи" width={340} interactive>
      <Stack gap={6}>
        <Text size="sm">
          Сколько спорных пар с каким советом судьи. Совет у строки виден только после решения по
          ней.
        </Text>
        <Text size="sm">
          «Применить советы…» ставит решения сразу всей полосе — «не пишем» или «пишем». «Не понять»
          остаётся человеку.
        </Text>
        <Text size="sm">
          Без совета — пары, которых судья ещё не разбирал: их берёт «Переобойти спорные».
        </Text>
        {mayApply ? null : (
          <Text size="sm" c="dimmed">
            Применяет сотрудник с правами «{PERMISSION_TITLES.run}» и «{PERMISSION_TITLES.prices}».
          </Text>
        )}
      </Stack>
    </InfoHint>
  );
}

/** Итог очереди одной строкой — под кнопками; очередь пуста — ничего. */
export function JudgeApplyLine() {
  const mayApply = useMayApply();
  const { data: standing } = useJudgeStanding();
  if (standing === undefined || standing.waiting === 0) return null;
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <Text size="sm" c="dimmed">
        Советы судьи: {standingWords(standing)}
      </Text>
      <About mayApply={mayApply} />
    </Group>
  );
}
