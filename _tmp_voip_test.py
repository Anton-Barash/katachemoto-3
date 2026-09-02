import sys
sys.path.insert(0, '.')
from app_web import prettify_message_content as p

sample = '<voipmsg type="VoIPBubbleMsg"><VoIPBubbleMsg><msg><![CDATA[Ответ уже опубликован]]></msg><room_type>1</room_type><red_dot>false</red_dot><roomid>1432006808915975406</roomid><roomkey>0</roomkey><inviteid>0</inviteid><msg_type>101</msg_type>'
print(repr(p(sample)))
sample2 = '<voipmsg type="VoIPBubbleMsg"><VoIPBubbleMsg><msg><![CDATA[Звонок отклонён]]></msg><duration>125</duration></VoIPBubbleMsg></voipmsg>'
print(repr(p(sample2)))
print(repr(p('<voipmsg><VoIPBubbleMsg><msg_type>102</msg_type></VoIPBubbleMsg></voipmsg>')))
print(repr(p('обычный текст сообщения')))
