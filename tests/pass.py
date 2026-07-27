'''
Randon test file not production 
'''





import bcrypt

sample = "samplePass"
pass_in_byte = sample.encode('utf-8')

salt = bcrypt.gensalt(rounds=12)
hashed = bcrypt.hashpw(pass_in_byte, salt)

test = "samplePass"
wrong = "sample"

print(bcrypt.checkpw(test.encode('utf-8'), hashed))
print(bcrypt.checkpw(wrong.encode('utf-8'), hashed))

