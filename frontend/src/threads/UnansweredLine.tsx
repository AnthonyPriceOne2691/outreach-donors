/**
 * Строка под списком «Диалогов» — где переписки без ответа: «Без ответа — 3: в «Письмах»»
 * (задача №2, решение 16.11.2026). В списке их нет (`threadAnswered.ts`), и без этой строки
 * казалось бы, что писем без ответа нет вовсе. Ссылка ведёт на «Письма» этапа: там такие переписки
 * собраны в список «Ждём ответа», и с каждой его строки можно открыть переписку.
 *
 * В части списка «Письма» названы коротко: чьи они, уже сказал заголовок. В общем списке доноры и
 * рекламодатели идут вместе, поэтому там своя строка у каждого этапа, где такие переписки есть, и
 * «Письма» в ней названы полным именем пункта меню. Нет таких переписок — нет и строки.
 *
 * Ссылка — чернилами с подчёркиванием (`inkLink`), как ссылки посреди текста на главной: бирюза на
 * светлом стекле не держала контраст. Число держится при подписи неразрывным пробелом.
 */

import { Anchor, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { PART_PARAM } from '../api/stages';
import type { Part } from '../api/stages';
import type { LetterStage } from '../api/types';
import { formatNumber } from '../format';
import type { Unanswered } from './threadAnswered';

/** «Письма» этапа в предложном падеже — полным именем пункта меню, для общего списка. Имя не
 *  рвётся переносом: на телефоне строка переносится перед ссылкой, а не посреди кавычек. */
const LETTERS_IN: Record<LetterStage, string> = {
  donors: '«Письмах\u00a0донорам»',
  advertisers: '«Письмах\u00a0рекламодателям»',
};

/** Слова ссылки: в части — «в «Письмах»», в общем списке — «в «Письмах донорам»». */
export function lettersWords(stage: LetterStage, part: Part | null): string {
  return `в\u00a0${part === null ? LETTERS_IN[stage] : '«Письмах»'}`;
}

interface Props {
  /** Переписки без ответа по этапам; пусто — строки нет. */
  silent: Unanswered[];
  /** Часть списка из адреса; `null` — общий список доноров и рекламодателей. */
  part: Part | null;
}

export function UnansweredLine({ silent, part }: Props) {
  if (silent.length === 0) return null;
  return (
    <Stack gap={2}>
      {silent.map(({ stage, count }) => (
        <Text key={stage} size="sm" c="dimmed" data-unanswered={stage}>
          {`Без ответа\u00a0—\u00a0${formatNumber(count)}: `}
          <Anchor
            component={Link}
            to={`/letters?${PART_PARAM}=${stage}`}
            size="sm"
            fw={500}
            c="var(--ink)"
            underline="always"
            className="inkLink"
          >
            {lettersWords(stage, part)}
          </Anchor>
        </Text>
      ))}
    </Stack>
  );
}
