/**
 * Коды восстановления — один раз (23.11.2026).
 *
 * Сервер хранит только их хеши: показать коды второй раз нечем. Поэтому экран не уходит, пока
 * человек сам не нажмёт «Я сохранил», и даёт два способа унести коды: скопировать и скачать
 * файлом. Каждый код пускает один раз вместо кода из приложения — когда телефона нет.
 *
 * Код — одной строкой (24.11.2026): шрифтом md в двух колонках узкой карточки он рвался по
 * дефисам на две строки, и переписать его с экрана было легко с ошибкой. Ступень sm и запрет
 * переноса: 19 знаков влезают в плитку двух колонок, а на узком экране колонка одна.
 */

import { Alert, Button, Code, CopyButton, Group, SimpleGrid, Stack, Text } from '@mantine/core';

import { saveFile } from '../api/donors';
import { SERVICE_NAME } from '../brand';

interface Props {
  codes: string[];
  note: string;
  account: string;
  onSaved: () => void;
}

/** Текст файла: чьи коды и как ими пользоваться — файл найдут через полгода. */
export function codesText(codes: string[], account: string): string {
  return [
    `${SERVICE_NAME} — коды восстановления для ${account}`,
    'Каждый код пускает один раз вместо кода из приложения-аутентификатора.',
    '',
    ...codes,
    '',
  ].join('\n');
}

export function RecoveryCodes({ codes, note, account, onSaved }: Props) {
  const download = () =>
    saveFile(
      {
        blob: new Blob([codesText(codes, account)], { type: 'text/plain;charset=utf-8' }),
        filename: null,
        headers: new Headers(),
      },
      'коды-восстановления.txt',
    );

  return (
    <Stack gap="md">
      <Alert color="yellow" title="Сохраните коды восстановления">
        {note}
      </Alert>
      <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="xs" aria-label="Коды восстановления">
        {codes.map((code) => (
          <Code
            key={code}
            fz="sm"
            ta="center"
            className="glassQuiet"
            p="xs"
            style={{ whiteSpace: 'nowrap' }}
          >
            {code}
          </Code>
        ))}
      </SimpleGrid>
      <Text size="sm" c="dimmed">
        Потеряли и телефон, и коды — вход с кодом сбросит админ.
      </Text>
      <Group gap="sm" justify="flex-end">
        <CopyButton value={codes.join('\n')}>
          {({ copied, copy }) => (
            <Button variant="default" className="press" onClick={copy}>
              {copied ? 'Скопировано' : 'Скопировать'}
            </Button>
          )}
        </CopyButton>
        <Button variant="default" className="press" onClick={download}>
          Скачать .txt
        </Button>
        <Button variant="gradient" className="press" onClick={onSaved}>
          Я сохранил
        </Button>
      </Group>
    </Stack>
  );
}
