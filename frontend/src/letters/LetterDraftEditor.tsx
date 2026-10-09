/**
 * Текст первого письма перед созданием рассылки — правка по зонам.
 *
 * **Правится содержимое, а не устройство.** Набор зон задан требованиями:
 * здесь нельзя ни добавить абзац, ни сделать условия переписываемыми.
 * У каждой зоны сказано, что с ней будет, — «переписывает модель» или
 * «уходит как есть»: иначе человек поправит приветствие и удивится,
 * что в письмах его правки не видно.
 *
 * **Проверяет сервер.** Разбор шаблона, подпись, достижимость коридора,
 * метрики — всё это делает тот же код, что разбирает шаблон из файла.
 * Второй набор правил здесь разошёлся бы с ним при первой правке.
 *
 * **Нетронутый текст не отправляется.** Тогда у новой рассылки — текст
 * по умолчанию, у найденной — её собственный, и одноимённая рассылка
 * дополняется, а не упирается в «у неё уже свой текст».
 *
 * **Текст — в окне, а не простынёй вниз** (замечание Anthony 09.10.2026): раскрытые
 * зоны отодвигали форму сборки на экран вниз. На странице — кнопка и отметка
 * «поправлен / по умолчанию»; правка живёт в состоянии формы и закрытием окна
 * не теряется.
 *
 * **Поля не кучкой** (замечание Anthony 09.10.2026, второй круг аудита): между
 * полями 24 px (`FIELD_GAP`), окно шире (780 px — строка письма в 70 знаков), что
 * модель сделает с зоной — пометкой в строке подписи, а что значат пометки — один
 * раз наверху; до этого фраза «Переписывает модель под каждого донора» стояла
 * отдельной строкой под четырьмя полями из семи. Прокручиваются поля, а не окно:
 * пояснение сверху и кнопки снизу видны всегда.
 */

import {
  Badge,
  Button,
  Group,
  Modal,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { IconPencil } from '@tabler/icons-react';
import { useState } from 'react';

import type { LetterDraft, LetterDraftView } from '../api/types';
import { ACTIONS_GAP, FIELD_GAP } from '../components/formRhythm';
import type { LetterTarget } from './targets';

export function draftOf(view: LetterDraftView): LetterDraft {
  return {
    subject: view.subject,
    zones: Object.fromEntries(view.zones.map((zone) => [zone.name, zone.text])),
  };
}

export function sameDraft(one: LetterDraft, other: LetterDraft): boolean {
  if (one.subject.trim() !== other.subject.trim()) return false;
  return Object.entries(other.zones).every(
    ([name, text]) => (one.zones[name] ?? '').trim() === text.trim(),
  );
}

interface Props {
  fallback: LetterDraftView;
  value: LetterDraft;
  onChange: (next: LetterDraft) => void;
  /** От адресата — подстановки и то, под кого модель переписывает зоны. */
  target?: LetterTarget;
}

/** Что можно подставить в текст и что нельзя трогать — по адресату. */
const HINTS: Record<LetterTarget, { placeholders: string; rewrite: string }> = {
  // `rewrite` — под кого модель пишет зону заново: «… под каждого донора».
  donors: {
    placeholders:
      '{{host}} — сайт донора, {{sender_name}} — имя в подписи. Пункты списка вопросов ' +
      'модель переписывает, но потерять не может: письмо с потерянным пунктом уходит с исходной ' +
      'формулировкой.',
    rewrite: 'каждого донора',
  },
  advertisers: {
    placeholders:
      '{{donor_host}} — площадка, где нашли ссылку, {{page_url}} — страница, {{anchor}} — анкор, ' +
      '{{donor_audience}} — фраза про страну аудитории площадки (пустая, если гео донора неизвестно), ' +
      '{{donor_country}} — сама страна по-английски (гео неизвестно — письмо с ней не уйдёт), ' +
      '{{sender_name}} — имя в подписи. Ссылка стоит только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы её пересказать, и сервер такой текст не примет. Цену донора не называть.',
    rewrite: 'каждого рекламодателя',
  },
  niche: {
    placeholders:
      '{{example_host}} — пример нашей площадки (принятый донор того же прогона со свежей ' +
      'ценой), {{niche}} — тема, по которой бизнес нашёлся в выдаче, {{donor_audience}} — фраза ' +
      'про страну аудитории (пустая, если страна прогона неизвестна), {{sender_name}} — имя в ' +
      'подписи. Пример площадки и тема стоят только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы их пересказать, и сервер такой текст не примет. Цену не называть.',
    rewrite: 'каждый бизнес',
  },
};

export function LetterDraftEditor({ fallback, value, onChange, target = 'donors' }: Props) {
  const [open, setOpen] = useState(false);
  const edited = !sameDraft(value, draftOf(fallback));

  const setZone = (name: string, text: string) =>
    onChange({ ...value, zones: { ...value.zones, [name]: text } });

  return (
    <Stack gap="sm" data-letter-draft>
      <Group gap="sm">
        <Button
          variant="subtle"
          className="press"
          aria-haspopup="dialog"
          leftSection={<IconPencil size={16} aria-hidden />}
          onClick={() => setOpen(true)}
        >
          Текст первого письма
        </Button>
        <Badge variant="light" color={edited ? 'yellow' : 'gray'}>
          {edited ? 'поправлен' : 'по умолчанию'}
        </Badge>
      </Group>

      {/* Поля — прямо на стекле окна, без вложенной подложки: поле во вложенном
          блоке — второй слой того же рецепта, фон светлел, и текст в поле тёмной
          темы намерился на 4,18 : 1 при 5,67 у поля прямо на карточке (23.09.2026). */}
      <Modal opened={open} onClose={() => setOpen(false)} title="Текст первого письма" size="xl">
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Текст закрепляется за рассылкой при её создании. «Модель переписывает» — зону модель
            пишет заново под {HINTS[target].rewrite}; «как есть» — уходит без изменений, модель её
            не видит. Подстановки: {HINTS[target].placeholders}
          </Text>
          {/* Прокручиваются поля, а не окно: шапка окна прозрачная, и поля уезжали
              бы под заголовок; кнопки снизу — на виду. Поле в 3 px от края держит
              кольцо фокуса внутри прокрутки. */}
          <ScrollArea.Autosize mah="calc(100dvh - 20rem)" type="auto" offsetScrollbars>
            <Stack gap={FIELD_GAP} p={3}>
              <TextInput
                label="Тема"
                value={value.subject}
                onChange={(event) => onChange({ ...value, subject: event.currentTarget.value })}
              />
              {fallback.zones.map((zone) => (
                <Textarea
                  key={zone.name}
                  label={zone.title}
                  description={zone.kind === 'rewrite' ? 'модель переписывает' : 'как есть'}
                  classNames={{
                    description: zone.kind === 'rewrite' ? 'zoneKind zoneRewrite' : 'zoneKind',
                  }}
                  autosize
                  minRows={1}
                  maxRows={8}
                  value={value.zones[zone.name] ?? ''}
                  onChange={(event) => setZone(zone.name, event.currentTarget.value)}
                />
              ))}
            </Stack>
          </ScrollArea.Autosize>
          <Group justify="space-between" mt={ACTIONS_GAP}>
            <Button
              variant="subtle"
              className="press"
              disabled={!edited}
              onClick={() => onChange(draftOf(fallback))}
            >
              Вернуть исходный текст
            </Button>
            <Button className="press" onClick={() => setOpen(false)}>
              Готово
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
