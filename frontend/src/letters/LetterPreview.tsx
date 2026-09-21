/**
 * Одно письмо: текст, процент отличия и три решения — отправить,
 * поправить, не писать.
 *
 * **Текст виден целиком, а не в виде сводки.** Смысл экрана в том, что
 * спорное решение видит человек; пересказ письма вместо письма этот
 * смысл отменяет.
 *
 * **Процент стоит рядом с текстом, а не в списке.** Число само по себе
 * ничего не говорит — говорит число рядом с текстом, к которому оно
 * относится, и вердикт словами рядом с числом.
 *
 * **Правка идёт в том же месте, где чтение.** Отдельное окно правки
 * прячет то, ради чего окно и открыли.
 *
 * **Добивки читаются здесь же, вкладками.** Согласуя первое письмо,
 * человек согласует всю цепочку: следующие два уйдут сами, по сроку.
 * Узнать о втором письме от донора — худший способ увидеть его текст.
 * Править их нельзя: текст добивки один на всю рассылку и живёт
 * шаблоном в коде, иначе у каждого письма заведётся своя правда.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Tabs,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useEffect, useState } from 'react';

import type { Corridor, QueuedLetter } from '../api/types';

export interface LetterPreviewProps {
  letter: QueuedLetter;
  corridor: Corridor;
  /** Чем заблокирована отправка. Непусто — кнопка не нажимается. */
  blockedBy: string[];
  /** Уходит ли письмо на самом деле. */
  transportIsReal: boolean;
  canSend: boolean;
  busy: boolean;
  onSend: () => void;
  onSkip: () => void;
  onSave: (subject: string, body: string) => void;
}

export function percentOf(share: number | null): string {
  return share === null ? '—' : `${Math.round(share * 100)}%`;
}

/** Зелёный — в коридоре, янтарь — нет. Те же два смысла, что и везде:
 *  «готово» и «нужно внимание». Третьего оттенка тут не бывает. */
export function toneOf(letter: QueuedLetter): 'green' | 'yellow' {
  return letter.verdict === null ? 'green' : 'yellow';
}

export function LetterPreview({
  letter,
  corridor,
  blockedBy,
  transportIsReal,
  canSend,
  busy,
  onSend,
  onSkip,
  onSave,
}: LetterPreviewProps) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(letter.subject ?? '');
  const [body, setBody] = useState(letter.body ?? '');
  const [tab, setTab] = useState<string>('first');

  // Смена письма сбрасывает правку: иначе текст одного донора уехал бы
  // в письмо другому — а это ровно та ошибка, которую уже не отозвать.
  useEffect(() => {
    setEditing(false);
    setSubject(letter.subject ?? '');
    setBody(letter.body ?? '');
    setTab('first');
  }, [letter.id, letter.subject, letter.body]);

  const followup = letter.followups.find((step) => `step${step.step}` === tab) ?? null;

  const blocked = blockedBy.length > 0;

  return (
    <Card className="glass" p="xl">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4}>
            <Text fw={600}>{letter.host}</Text>
            <Text size="xs" c="dimmed">
              {letter.email ?? 'адрес не определён'} · кампания «{letter.campaign}»
            </Text>
          </Stack>
          {/* Не ужимается: на узком окне значок сжимался до «отли…»,
              то есть до слова, которое ничего не значит. */}
          <Badge variant="light" color={toneOf(letter)} style={{ flexShrink: 0 }}>
            отличие {percentOf(letter.uniqueness)}
          </Badge>
        </Group>

        {letter.verdict !== null ? (
          <Alert color="yellow" title="Отличие вне коридора">
            {letter.verdict}. Коридор — {Math.round(corridor.min * 100)}–
            {Math.round(corridor.max * 100)}%. Письмо можно отправить и таким, но лучше поправить.
          </Alert>
        ) : null}

        <Tabs value={tab} onChange={(value) => setTab(value ?? 'first')} variant="pills">
          <Tabs.List>
            <Tabs.Tab value="first">Первое письмо</Tabs.Tab>
            {letter.followups.map((step) => (
              <Tabs.Tab key={step.step} value={`step${step.step}`}>
                Добивка {step.step}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>

        {followup !== null ? (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Уйдёт сама через {followup.in_days} дн. после предыдущего письма — если донор не
              ответит, не отпишется и письмо не вернётся отказом доставки.
            </Text>
            <Text fw={500}>{followup.subject}</Text>
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {followup.body}
            </Text>
            <Text size="xs" c="dimmed">
              Текст добивки один на всю рассылку и правится шаблоном в коде: модель его не трогает.
            </Text>
          </Stack>
        ) : editing ? (
          <Stack gap="sm">
            <TextInput
              label="Тема"
              value={subject}
              onChange={(event) => setSubject(event.currentTarget.value)}
            />
            <Textarea
              label="Текст письма"
              autosize
              minRows={12}
              value={body}
              onChange={(event) => setBody(event.currentTarget.value)}
            />
            <Text size="xs" c="dimmed">
              Проценты пересчитаются после сохранения — от шаблона, который лежит сейчас.
            </Text>
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text fw={500}>{letter.subject}</Text>
            {/* Письмо — обычный текст, и переносы в нём значимые: абзацы
                задают его ритм, а свёрнутое в одну строку письмо читается
                иначе, чем уйдёт адресату. */}
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {letter.body}
            </Text>
          </Stack>
        )}

        {!transportIsReal && followup === null ? (
          <Alert color="yellow" title="Наружу письмо не уйдёт">
            Транспорт не настроен: письмо будет помечено отправленным, но адресат его не получит.
            Работает только на выдуманных доменах.
          </Alert>
        ) : null}

        {/* Короче, чем предупреждение наверху, и не пересказывает его:
            там сказано, почему так, здесь — почему не нажимается эта
            кнопка. Два одинаковых текста на одном экране читаются
            как сбой, а не как забота. */}
        {blocked && followup === null ? (
          <Text size="sm" c="dimmed">
            Кнопка не нажимается: не заполнено {blockedBy.length} из обязательных настроек —
            подробности наверху экрана.
          </Text>
        ) : null}

        {/* Кнопки — действие, а не третье поле: отделяются отступом.
            На вкладке добивки их нет вовсе: отправить её по кнопке
            нельзя (уйдёт по сроку), а править — значит править шаблон.

            Именно не отрисовываются, а не прячутся атрибутом `hidden`:
            у Mantine на этой группе стоит `display: flex`, и он сильнее
            умолчания `[hidden] { display: none }`. В jsdom кнопка при
            этом считается скрытой, и тест проходит — а в браузере она
            видна. Поймано снимком. */}
        {followup === null ? (
          <Group gap="sm" mt="sm">
            {editing ? (
              <>
                <Button
                  color="lagoon"
                  loading={busy}
                  className="press"
                  onClick={() => onSave(subject, body)}
                >
                  Сохранить
                </Button>
                <Button variant="subtle" color="gray" onClick={() => setEditing(false)}>
                  Отменить правку
                </Button>
              </>
            ) : (
              <>
                <Button
                  color="lagoon"
                  disabled={!canSend || blocked}
                  loading={busy}
                  className="press"
                  onClick={onSend}
                >
                  Отправить
                </Button>
                <Button
                  variant="light"
                  color="lagoon"
                  disabled={!canSend}
                  className="press"
                  onClick={() => setEditing(true)}
                >
                  Поправить
                </Button>
                <Button
                  variant="subtle"
                  color="red"
                  disabled={!canSend}
                  className="press"
                  onClick={onSkip}
                >
                  Не писать
                </Button>
              </>
            )}
          </Group>
        ) : null}

        {!canSend && followup === null ? (
          <Text size="xs" c="dimmed">
            Отправка — отдельное право, его выдают поимённо. Смотреть очередь можно всем.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}
