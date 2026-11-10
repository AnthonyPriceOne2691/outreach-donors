/**
 * Текст недельного отчёта на вкладке «Отчёт»: восемь разделов — свёрнуто, что ждёт действия —
 * на виду, похожие строки — одной.
 *
 * **Разделы — по нажатию** (замечание владельца 10.11.2026 об экранах продаж: «пояснения — не
 * простынёй, а свёрнуто или в «i»»). Аудит прода насчитал на вкладке 488 слов: восемь разделов —
 * тот же текст, что уходит в группу продаж, — повторяли плитки и таблицу по гипотезам. Теперь они —
 * строкой-кнопкой под таблицей (`aria-expanded` и `aria-controls`; свёрнутое не отрисовывается —
 * `runs/Unfold`), как причины отсева в отчёте сбора (`SegmentReport`). Не карточкой `FoldCard`:
 * вкладка уже стоит на стекле панели, второй слой стекла читается грязью (`docs/UI_RULES.md`,
 * «Поверхности»). Надпись — «для группы продаж», а не «как ушёл в группу»: отчёт может и не уйти
 * (отправка выключена, Telegram отказал — это строка над плитками), а текст тот же.
 *
 * **Что ждёт действия — не за раскрытием.** Свёрнутое отвечает на вопрос «всё ли в порядке», а то,
 * по чему решают сейчас, видно без раскрытия (`docs/UI_RULES.md`, «Плитки, поля и карточки»). В
 * отчёте это проблема с поводом для беспокойства — «Есть повод для беспокойства — почему: что
 * сделать». Отбирает такие строки сервер (`cause_lines`), а не экран по словам; на экране они —
 * плашкой янтарём под плитками: тот же цвет, что у метки «есть повод» у тревог над вкладками. Мерка —
 * повод, а не `now` («(сейчас)»): `now` говорит, когда снято состояние, а не нужно ли действие. Ящик,
 * вставший на паузу в среду и всё ещё стоящий, — событие недели с поводом; передача, которую Kommo
 * повторит сам, — «сейчас» без повода. Повода нет ни у одной строки — плашки нет: так и тревоги над
 * вкладками — тихое не висит (слово владельца 02.11.2026).
 *
 * **Похожие строки — одной, их строки — по «показать N»** (аудит: шесть почти одинаковых строк о
 * паузах ящиков, 162 слова). Складывает сервер (`details`): экран слов не разбирает и не считает.
 */

import { Alert, Anchor, Box, Button, Group, List, Stack, Text } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';

import type { WeeklySection } from '../api/salesWeekly';
import { formatNumber } from '../format';
import { Unfold } from '../runs/Unfold';

/** Строка списка раздела. В сложенную сервером — её строки по «показать N»: ссылкой чернилами сразу
 *  за словами, как «показать целиком» у описаний (`ClampText`). */
function ReportLine({ text, details }: { text: string; details: string[] }) {
  const [open, setOpen] = useState(false);
  const list = useId();
  if (details.length === 0) return <List.Item>{text}</List.Item>;
  return (
    <List.Item>
      {text}{' '}
      <Anchor
        component="button"
        type="button"
        size="sm"
        c="var(--ink)"
        underline="always"
        className="inkLink weeklyMore"
        aria-expanded={open}
        aria-controls={list}
        onClick={() => setOpen((was) => !was)}
      >
        {open ? 'свернуть' : `показать ${formatNumber(details.length)}`}
      </Anchor>
      <Unfold open={open}>
        <List id={list} size="sm" spacing={2} mt={4} withPadding className="weeklyDetails">
          {details.map((line, at) => (
            <List.Item key={at}>{line}</List.Item>
          ))}
        </List>
      </Unfold>
    </List.Item>
  );
}

/** Строки раздела с этими номерами в `lines` — списком, со сложенными в них строками. Ключ — номер
 *  и слова: другая неделя на том же месте — другая строка, и раскрытой она не наследуется. */
function Lines({ section, at }: { section: WeeklySection; at: number[] }) {
  return (
    <List size="sm" spacing={2} mt={4} className="wrapCell">
      {at.map((index) => {
        const line = section.lines[index] ?? '';
        return (
          <ReportLine key={`${index}:${line}`} text={line} details={section.details[index] ?? []} />
        );
      })}
    </List>
  );
}

/** Разделы — теми же словами, что в группе: первая строка — итог, дальше — список. */
function Sections({ sections }: { sections: WeeklySection[] }) {
  return (
    <Stack gap="sm" className="weeklySections">
      {sections.map((section, index) => (
        <Box key={section.key} className="weeklySection">
          <Text size="sm" fw={600}>
            {index + 1}. {section.title}
          </Text>
          <Text size="sm" className="wrapCell">
            {section.lines[0]}
          </Text>
          {section.lines.length > 1 ? (
            <Lines section={section} at={section.lines.slice(1).map((_, at) => at + 1)} />
          ) : null}
        </Box>
      ))}
    </Stack>
  );
}

/** Восемь разделов отчёта — свёрнуто, по нажатию. */
export function ReportText({ sections }: { sections: WeeklySection[] }) {
  const [open, setOpen] = useState(false);
  const body = useId();
  return (
    <Stack gap="xs" px="md">
      <Group>
        <Button
          variant="subtle"
          size="compact-sm"
          px={6}
          className="press weeklyTextToggle"
          aria-expanded={open}
          aria-controls={body}
          leftSection={
            <IconChevronDown
              size={16}
              aria-hidden
              style={{ transform: open ? 'rotate(180deg)' : 'none' }}
            />
          }
          onClick={() => setOpen((was) => !was)}
        >
          Текст отчёта для группы продаж
        </Button>
      </Group>
      <Unfold open={open}>
        <Box id={body}>
          <Sections sections={sections} />
        </Box>
      </Unfold>
    </Stack>
  );
}

/** Строки с поводом для беспокойства — из всех разделов, номерами сервера; нет их — ничего. */
export function WeeklyNow({ sections }: { sections: WeeklySection[] }) {
  const marked = sections.filter((section) => section.cause_lines.length > 0);
  const count = marked.reduce((sum, section) => sum + section.cause_lines.length, 0);
  if (count === 0) return null;
  return (
    <Alert color="yellow" py={6} px="sm" mx="md" className="weeklyNow">
      <Text size="sm" fw={600}>
        Ждут действия — {formatNumber(count)}
      </Text>
      {marked.map((section) => (
        <Lines key={section.key} section={section} at={section.cause_lines} />
      ))}
    </Alert>
  );
}
