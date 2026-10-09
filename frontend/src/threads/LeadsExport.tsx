/**
 * Лиды файлом — ответы людей на оффер рекламодателю, те же поля, что уходят
 * вебхуком в CRM. Без настроенного вебхука это и есть путь передачи лида.
 *
 * В шапке списка рядом с перепиской (`ThreadList`) места на подпись нет — там
 * значок со стрелкой вниз и подсказкой; имя у кнопки то же, что у подписи.
 */

import { ActionIcon, Button, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDownload } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { exportCounts, saveFile } from '../api/donors';
import { exportLeads } from '../api/outreach';
import { useSession } from '../auth/AuthProvider';

const TITLE = 'Выгрузить лиды';

export function LeadsExport({ compact = false }: { compact?: boolean }) {
  const { can } = useSession();
  const leads = useMutation({
    mutationFn: exportLeads,
    onSuccess: (file) => {
      saveFile(file, 'leads.csv');
      const rows = exportCounts(file).rows;
      const truncated = file.headers.get('X-Export-Truncated') === '1';
      notifications.show({
        message:
          rows === 0
            ? 'Лидов пока нет — файл пустой'
            : truncated
              ? `Выгружено лидов: ${rows ?? '—'} — это потолок, часть лидов не вошла`
              : `Выгружено лидов: ${rows ?? '—'}`,
        ...(truncated ? { color: 'yellow' } : {}),
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Выгрузка не удалась',
        message: refusalOf(failure),
        color: 'red',
      }),
  });
  // Файл отдаётся под правом prices — без него кнопки нет, а не отказ после нажатия.
  if (!can('prices')) return null;
  if (compact) {
    return (
      <Tooltip label={TITLE} withArrow>
        <ActionIcon
          variant="subtle"
          aria-label={TITLE}
          loading={leads.isPending}
          onClick={() => leads.mutate()}
        >
          <IconDownload size={18} />
        </ActionIcon>
      </Tooltip>
    );
  }
  return (
    <Button variant="light" size="xs" loading={leads.isPending} onClick={() => leads.mutate()}>
      {TITLE}
    </Button>
  );
}
