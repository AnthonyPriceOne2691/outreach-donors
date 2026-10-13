/**
 * Текст первого письма перед созданием рассылки — правка по зонам.
 *
 * **Правится содержимое, а не устройство.** Набор зон задан требованиями:
 * здесь нельзя ни добавить абзац, ни сделать условия переписываемыми.
 * У каждой зоны сказано, что с ней будет, — «может переписать модель» или
 * «уходит как есть»: иначе человек поправит приветствие и удивится,
 * что в письмах его правки не видно. «Может», а не «переписывает»: подбор под
 * коридор отличия принимает не все переписанные зоны (`letters/selection.py`).
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
 * отдельной строкой под четырьмя полями из семи.
 *
 * **В окне одна прокрутка** (замечание Anthony 12.10.2026: «двойная прокрутка… прям
 * косяк»): заголовок окна и кнопки стоят, прокручиваются поля (`oneScroll` в glass.css).
 * Поле растёт по тексту и своей прокрутки не имеет. Было три вложенных: окно (Mantine
 * держит его в 90 % высоты экрана), поля под ним (`100dvh − 20rem` — запас меньше, чем
 * занимали пояснение, шапка и кнопки) и «Вопросы» — длиннее восьми строк на телефоне;
 * кнопки уезжали под край экрана.
 *
 * **Пояснение стоит от md и шире** (правило 10.10.2026: что значат пометки, видно, пока
 * правишь любую зону; `HINT_FIXED`), а уже — уезжает в ту же прокрутку первым абзацем:
 * на телефоне оно в 284–426 px из 760 высоты окна, и неподвижное оставляло полям щель.
 */

import { Badge, Box, Button, Group, Modal, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
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
      '{{host}} — сайт донора, {{sender_name}} — имя в подписи, {{postal_address}} — почтовый ' +
      'адрес под ним, убрать его нельзя. Пункты списка вопросов ' +
      'модель переписывает, но потерять не может: письмо с потерянным пунктом уходит с исходной ' +
      'формулировкой.',
    rewrite: 'каждого донора',
  },
  advertisers: {
    placeholders:
      '{{donor_host}} — площадка, где нашли ссылку, {{page_url}} — страница, {{anchor}} — анкор, ' +
      '{{donor_audience}} — фраза про страну аудитории площадки (пустая, если гео донора неизвестно), ' +
      '{{donor_country}} — сама страна по-английски (гео неизвестно — письмо с ней не уйдёт), ' +
      '{{sender_name}} — имя в подписи, {{postal_address}} — почтовый адрес под ним, убрать его ' +
      'нельзя. Ссылка стоит только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы её пересказать, и сервер такой текст не примет. Цену донора не называть.',
    rewrite: 'каждого рекламодателя',
  },
  niche: {
    placeholders:
      '{{example_host}} — пример нашей площадки (принятый донор того же прогона со свежей ' +
      'ценой), {{niche}} — тема, по которой бизнес нашёлся в выдаче, {{donor_audience}} — фраза ' +
      'про страну аудитории (пустая, если страна прогона неизвестна), {{sender_name}} — имя в ' +
      'подписи, {{postal_address}} — почтовый адрес под ним, убрать его нельзя. Пример площадки ' +
      'и тема стоят только в неизменяемых зонах: в переписываемой ' +
      'модель могла бы их пересказать, и сервер такой текст не примет. Цену не называть.',
    rewrite: 'каждый бизнес',
  },
};

/** Где пояснение окна стоит над полями: от `md` Mantine и при высоте, где под ним остаётся
 *  место полям. Ниже 40em неподвижное пояснение, шапка и кнопки съели бы окно (90 % экрана),
 *  и вернулась бы вторая прокрутка — самого окна. */
export const HINT_FIXED = '(min-width: 62em) and (min-height: 40em)';

export function LetterDraftEditor({ fallback, value, onChange, target = 'donors' }: Props) {
  const [open, setOpen] = useState(false);
  // С первой отрисовки, как у рамы: окно открывают позже, но значение уже верное.
  const hintFixed = useMediaQuery(HINT_FIXED, false, { getInitialValueInEffect: false }) === true;
  const edited = !sameDraft(value, draftOf(fallback));

  const setZone = (name: string, text: string) =>
    onChange({ ...value, zones: { ...value.zones, [name]: text } });

  const hint = (
    <Text size="sm" c="dimmed">
      Текст закрепляется за рассылкой при её создании. «Может переписать модель» — зону модель пишет
      заново под {HINTS[target].rewrite}, но в отдельном письме она может остаться текстом шаблона:
      так письмо ложится в коридор отличия. «Как есть» — уходит без изменений, модель её не видит.
      Подстановки: {HINTS[target].placeholders}
    </Text>
  );

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
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Текст первого письма"
        size="xl"
        classNames={{ content: 'oneScroll' }}
      >
        {/* Прокрутка одна — поля; пояснение над ней стоит (`HINT_FIXED`), а на узком
            экране идёт в ней первым абзацем. Шапка окна прозрачная, и под ней ничего не
            проезжает: прокрутка начинается ниже. Поле в 3 px от края держит кольцо фокуса
            внутри прокрутки — на те же 3 px отступает и неподвижное пояснение. */}
        {hintFixed ? (
          <Box px={3} mb="md">
            {hint}
          </Box>
        ) : null}
        <div className="oneScrollArea scrollSlim">
          <Stack gap="md" p={3}>
            {hintFixed ? null : hint}
            <Stack gap={FIELD_GAP}>
              <TextInput
                label="Тема"
                value={value.subject}
                onChange={(event) => onChange({ ...value, subject: event.currentTarget.value })}
              />
              {fallback.zones.map((zone) => (
                // Без `maxRows`: поле длиннее потолка прокручивалось само — вторая
                // прокрутка внутри окна (на телефоне — «Вопросы» донорам).
                <Textarea
                  key={zone.name}
                  label={zone.title}
                  description={zone.kind === 'rewrite' ? 'может переписать модель' : 'как есть'}
                  classNames={{
                    description: zone.kind === 'rewrite' ? 'zoneKind zoneRewrite' : 'zoneKind',
                  }}
                  autosize
                  minRows={1}
                  value={value.zones[zone.name] ?? ''}
                  onChange={(event) => setZone(zone.name, event.currentTarget.value)}
                />
              ))}
            </Stack>
          </Stack>
        </div>
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
      </Modal>
    </Stack>
  );
}
