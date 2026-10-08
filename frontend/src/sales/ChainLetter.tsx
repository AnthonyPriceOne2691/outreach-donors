/**
 * Письмо глазами адресата — ответ сервера на «Показать письмо».
 *
 * **Порядок — тот, в каком письмо соберёт сборка**: от кого, тема, зоны с
 * выдуманными значениями, подпись и физический адрес из «Отправителя». У каждой
 * зоны сказано, что с ней будет — «переписывает модель» или «уходит как есть»:
 * иначе человек поправит приветствие и удивится, что в письмах правки не видно.
 *
 * **Чего не хватает для отправки — словами сервера**, тем же правилом, каким
 * откажет отправка; незаданная подпись или адрес видны на своём месте в письме.
 */

import { Alert, Badge, Group, Stack, Text } from '@mantine/core';

import { chainPlaceholderTitle, ZONE_KINDS } from '../api/salesLabels';
import type { ChainPreviewView, ZoneCard } from '../api/salesTypes';

function Zone({ zone }: { zone: ZoneCard }) {
  return (
    <Stack gap={4} className="chainZone">
      <Group gap="xs">
        <Text size="xs" c="dimmed">
          {zone.name}
        </Text>
        <Badge variant="light" color={zone.kind === 'rewrite' ? 'blue' : 'gray'}>
          {ZONE_KINDS[zone.kind]}
        </Badge>
      </Group>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {zone.text}
      </Text>
    </Stack>
  );
}

/** Подпись или адрес из настроек — или слова о том, что их нет. */
function FromSettings({ text, absent }: { text: string | null; absent: string }) {
  if (text === null) {
    return (
      <Text size="sm" c="dimmed">
        {absent} — заполните вкладку «Отправитель»
      </Text>
    );
  }
  return (
    <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} className="chainSigned">
      {text}
    </Text>
  );
}

export function ChainLetter({ letter }: { letter: ChainPreviewView }) {
  const values = Object.entries(letter.values).map(
    ([name, value]) => `${chainPlaceholderTitle(name)} — ${value}`,
  );
  return (
    <Stack gap="sm" className="chainLetter" aria-label="Письмо глазами адресата">
      <Text size="sm">
        <b>От:</b> {letter.sender_name ?? 'имя отправителя не задано'}
      </Text>
      <Text size="sm">
        <b>Тема:</b> {letter.subject ?? 'тема первого письма — добивка уходит в той же переписке'}
      </Text>
      {letter.zones.map((zone) => (
        <Zone key={zone.name} zone={zone} />
      ))}
      <FromSettings text={letter.signature} absent="Подпись не задана" />
      <FromSettings text={letter.address} absent="Физический адрес не задан" />
      {letter.missing.length > 0 && (
        <Alert color="yellow" title="Отправка продаж не готова">
          {letter.missing.join('; ')} — без этого письмо продаж не уходит.
        </Alert>
      )}
      <Text size="xs" c="dimmed" className="chainValues">
        Для примера подставлено: {values.join('; ')}.
      </Text>
    </Stack>
  );
}
